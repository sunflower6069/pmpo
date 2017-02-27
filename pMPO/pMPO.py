# Apache License 2.0
#
# Copyright (c) 2017 Merck & Co, Inc.
#
# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the LICENSE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied.  See the License for the
# specific language governing permissions and limitations
# under the License.

import numpy as np
import pandas as pd
from scipy import stats
from abc import ABCMeta

########################################################################################################################
# The following functions wrap the functionality described in:
# Gunaydin, Hakan. Probabilistic Approach to Generating MPOs and Its Application as a Scoring Function for CNS Drugs.
# ACS Med Chem Lett 7, 89-93 (2016)
########################################################################################################################


def build_evaluator(good_value='default'):
    """
    Build an evaluator to determine TRUE values in a DataFrame column
    :param good_value: Value of TRUE rows ['default': is an assortment of common words and values that evaluate to TRUE]
    :return: A function that when given a series object evaluates whether the good_column is TRUE
    """
    DEFAULT_TRUTHS = {'true', 'True', 'TRUE', 't', 'T',
                      'good', 'Good', 'GOOD', 'g', 'G',
                      'active', 'Active', 'ACTIVE', 'a', 'A',
                      'yes', 'Yes', 'YES', 'y', 'Y',
                      '1', 1, True}
    if good_value == 'default':
        return lambda x: x in DEFAULT_TRUTHS
    else:
        return lambda x: x == good_value


def numeric_column_iterator(df: pd.DataFrame):
    """
    Generator over all the numeric columns names in a Pandas DataFrame
    :param df: A Pandas DataFrame
    :return: The string names of the numeric columns in the DataFrame
    """
    for col, dt in df.dtypes.to_dict().items():
        # noinspection PyUnresolvedReferences
        if np.issubdtype(dt, np.number):
            yield col


def cutoff_fn(good_mean: float, good_std: float, bad_mean: float, bad_std: float) -> float:
    """
    Calculate the cutoff value
    :param good_mean: Mean of the good samples
    :param good_std: Standard deviation of the good samples
    :param bad_mean: Mean of the bad samples
    :param bad_std: Standard deviation of the bad samples
    :return: The cutoff value
    """
    if good_mean < bad_mean:
        return ((bad_mean - good_mean) / (good_std + bad_std)) * good_std + good_mean
    else:
        return ((good_mean - bad_mean) / (good_std + bad_std)) * bad_std + bad_mean


def calculate_descriptor_statistics(df: pd.DataFrame, good_column: str, min_samples=10, p_cutoff=0.01,
                                    q_cutoff=0.05, ignore_columns=None) -> pd.DataFrame:
    """
    Calculate the separation statistics between good and bad molecules on each descriptor column
    The output DataFrame has the following columns:
    name, p_value, good_mean, good_std, good_nsamples, bad_mean, bad_std, bad_nsamples
    :param df: Input DataFrame with good molecules, bad molecules, and data
    :param good_column: Input DataFrame column that distinguishes good from bad values
    :param min_samples: The minimum number of samples with good or bad data to calculate p-value statistics
    :param p_cutoff: The p-value cutoff to determine significant separation between good and bad molecules
    :param q_cutoff: The q-value cutoff used in parameterizing the sigmoidal functions
    :param ignore_columns: List of columns to ignore in the DataFrame
    :return: A Pandas DataFrame with summary statistics sorted by p-value
    """
    data = []
    for col in numeric_column_iterator(df):
        # Skip columns we're explicitly asked to ignore
        if ignore_columns is not None and col in ignore_columns:
            continue
        # Create a sub-DataFrame with just the good/bad indicator and this numeric column
        subdf = df[[good_column, col]].dropna(thresh=2)
        # Get arrays of values for the good/bad samples
        good = subdf[(subdf[good_column] == True)][[col]].values
        bad = subdf[(subdf[good_column] == False)][[col]].values
        # Generate statistics from the values
        if good.size >= min_samples and bad.size >= min_samples:
            data.append(
                {
                    'name': col,
                    'p_value': stats.ttest_ind(good, bad, equal_var=False)[1][0],
                    'good_mean': np.mean(good),
                    'good_std': np.std(good),
                    'good_nsamples': len(good),
                    'bad_mean': np.mean(bad),
                    'bad_std': np.std(bad),
                    'bad_nsamples': len(bad)
                }
            )
    column_stats = pd.DataFrame(data)
    # Determine statistically significant columns
    column_stats['significant'] = column_stats['p_value'] < p_cutoff
    # Calculate the cutoff
    column_stats['cutoff'] = np.where(column_stats['good_mean'] < column_stats['bad_mean'],
                                      # Case: good_mean < bad_mean
                                      # bad_mean - good_mean
                                      # -------------------- * good_std + good_mean
                                      # good_std + bad_std
                                      ((column_stats['bad_mean'] - column_stats['good_mean']) /
                                       (column_stats['good_std'] + column_stats['bad_std'])) *
                                      column_stats['good_std'] + column_stats['good_mean'],
                                      # Case: good_mean >= bad_mean
                                      # good_mean - bad_mean
                                      # -------------------- * bad_std + bad_mean
                                      # good_std + bad_std
                                      ((column_stats['good_mean'] - column_stats['bad_mean']) /
                                       (column_stats['good_std'] + column_stats['bad_std'])) *
                                      column_stats['bad_std'] + column_stats['bad_mean'])
    # Sigmoidal function:
    #               1
    # f(x) = -----------------
    #                -1(x-<x>)
    #         1 + b c
    # We need to calculate the parameters of this function: b, c
    # First using a gaussian form: a*numpy.exp(-(x-b)**2/(2*c**2)) to solve for the inflection point
    # Where: a = 1.0
    #        x = the calculated cutoff
    #        b = descriptor mean in good molecules (not the same b as the sigmodal function above)
    #        c = descriptor std in good molecules (not the same c as the sigmoidal function above)
    column_stats['inflection'] = np.exp(-np.square((column_stats['cutoff'] - column_stats['good_mean'])) /
                                        (2 * np.square(column_stats['good_std'])))
    # Calculate the b in the sigmoidal function
    column_stats['b'] = 1.0 / column_stats['inflection'] - 1.0
    # q-value cutoff transformation
    n = 1.0 / q_cutoff - 1.0
    # Calculate the c in the sigmoidal function
    column_stats['c'] = np.power(10.0, ((np.log10(n / column_stats['b'])) /
                                        (-1.0 * (column_stats['bad_mean'] - column_stats['cutoff']))))
    # Calculate the cutoff Z-score
    column_stats['z'] = np.absolute(column_stats['cutoff'] - column_stats['good_mean']) / column_stats['good_std']
    return column_stats.sort_values(by='p_value')


def pick_uncorrelated_columns(df: pd.DataFrame, column_stats: pd.DataFrame, r2_cutoff=0.53, resort=False) -> pd.DataFrame:  # noqa
    """
    Calculate the descriptor r^2 correlation matrix and select uncorrelated descriptors by p-value
    :param df: The main Pandas DataFrame
    :param column_stats: The Pandas DataFrame with the column summary statistics
    :param r2_cutoff: Threshold R^2 that defines correlated descriptors
    :param resort: Whether to resort the column statistics
    :return: Tuple of the descriptor corelation matrix and selected descriptors
    """
    if resort:
        df = df.sort_values(by='p_value')
    # Calculate the r^2 correlation matrix for all the statistically significant columns
    significant_columns = column_stats[(column_stats.significant == True)]
    desc_correlation = np.square(df[significant_columns.name.values.tolist()].corr())
    # Pick the descriptors with the highest separation and do not select correlated descriptors
    selected_descriptors = set()
    for row in significant_columns.iterrows():
        this_desc = row[1]['name']
        # Do not select this descriptor if it is correlated to a higher priority descriptor
        if not any(desc_correlation[this_desc][that_desc] > r2_cutoff for that_desc in selected_descriptors):
            selected_descriptors.add(this_desc)
    # Annotate selected descriptors
    column_stats['selected'] = column_stats['significant'] & column_stats['name'].isin(selected_descriptors)
    return desc_correlation


def calculate_descriptor_weights(df):
    z_sum = df[(df['selected'] == True)].z.sum()
    df['w'] = np.where(df['selected'] == True, df['z'] / z_sum, np.nan)

########################################################################################################################
# More convenient classes for model building
########################################################################################################################


class pMPOFunction:
    """
    The base class for any pMPOFunction.#
    Mostly for flexible extension of all pMPO functions down the line.
    """
    __metaclass__ = ABCMeta

    def __call__(self, val):
        raise NotImplemented("pMPOFunction {} not fully implemented".format(type(self)))


class pMPOModel:

    def __init__(self, name):
        self.name = name
        self.functions = {}

    def __call__(self, **kwargs):
        return sum(self.functions[key](val) if (key in self.functions and not np.isnan(val)) else 0.0
                   for key, val in kwargs.items())

    def register(self, name: str, fn: pMPOFunction):
        self.functions[name] = fn

    def __str__(self):
        submodels = sorted(["[{}] {}".format(name, str(fn)) for name, fn in self.functions.items()])
        return "{}: {}".format(self.name, " + ".join(submodels))


class SigmoidalFunction(pMPOFunction):
    def __init__(self, **kwargs):
        if 'mean' not in kwargs:
            raise KeyError(" mean not provided to sigmoidal pMPO function: {}".format(kwargs))
        if 'weight' not in kwargs:
            raise KeyError("weight not provided to sigmoidal pMPO function: {}".format(kwargs))
        if 'std' not in kwargs:
            raise KeyError("std not provided to sigmoidal pMPO function: {}".format(kwargs))
        try:
            self.mean = float(kwargs['mean'])
        except ValueError:
            raise KeyError("mean to sigmoidal pMPO function cannot be cast to float".format(kwargs['mean']))
        try:
            self.std = float(kwargs['std'])
        except ValueError:
            raise KeyError("std to sigmoidal pMPO function cannot be cast to float".format(kwargs['std']))
        try:
            self.weight = float(kwargs['weight'])
        except ValueError:
            raise KeyError("weight to sigmoidal pMPO function cannot be cast to float".format(kwargs['weight']))

    def __call__(self, val):
        return self.weight * np.exp(-1.0 * np.square(val - self.mean) / (2.0 * np.square(self.std)))

    def __str__(self):
        return "{:.2f} * np.exp(-1.0 * (x - {:.2f})^2 / (2.0 * ({:.2f})^2))".format(self.weight, self.mean, self.std)


class pMPOBuilder:
    """
    Build a pMPO model
    """
    def __init__(self, df: pd.DataFrame, good_column: str, model_name: str, good_value='default',
                 pMPO_good_column_name: str=None, min_samples: int=10, p_cutoff: float=0.01, q_cutoff: float=0.05,
                 r2_cutoff: float=0.53):
        """
        Build a pMPO model
        :param df: Input DataFrame with good molecules, bad molecules, and data
        :param good_column: Input DataFrame column that distinguishes good from bad values
        :param model_name: Name of the pMPO model
        :param good_value: Criteria to evaluate good from bad molecules ('default', str, callable)
        :param pMPO_good_column_name: Optional boolean column name for good molecules [default: 'pMPO_POSITIVE']
        :param min_samples: The minimum number of samples with good or bad data to calculate p-value statistics
        :param p_cutoff: The p-value cutoff to determine significant separation between good and bad molecules
        :param q_cutoff: The q-value cutoff used in parameterizing the sigmoidal functions
        :param r2_cutoff: The r^2 cutoff for determining linearly correlated descriptors
        """
        # -------------------------------------
        # | Set up the input Pandas DataFrame |
        # -------------------------------------
        self.df = df
        self.min_samples = min_samples
        self.p_cutoff = p_cutoff
        self.q_cutoff = q_cutoff
        self.r2_cutoff = r2_cutoff
        self.pMPO_model_name = model_name
        self.pMPO = None
        # The good_column name parameter is the name of the column specifying whether molecules are good or bad in the
        # input dataset. We want to have a boolean column in Pandas, which is going to be self.good_column
        if pMPO_good_column_name is None:
            self.good_column = "pMPO_POSITIVE"
        else:
            self.good_column = pMPO_good_column_name
        # Create the evaluator that evaluates whether an individual value in the input good_column is True
        # if good_value == 'default':
        #     good_value_evaluator = lambda row: row[self.good_column] in DEFAULT_TRUTHS
        # else:
        #     good_value_evaluator = lambda x: str(row[go]) == good_value
        good_value_evaluator = build_evaluator(good_value)
        # Apply the evaluator to the input good_column and store it in the renamed self.good_column
        self.df[self.good_column] = np.vectorize(good_value_evaluator)(self.df[good_column])
        # ---------------------------
        # | Do the pMPO calculation |
        # ---------------------------
        # Calculate the ability of each descriptor to separate good from bad
        self.decriptor_stats = calculate_descriptor_statistics(self.df,
                                                               good_column=self.good_column,
                                                               min_samples=self.min_samples,
                                                               p_cutoff=self.p_cutoff,
                                                               q_cutoff=self.q_cutoff)
        # Calculate correlated descriptors
        # Note: Adds the critical "selected" column to self.descriptor_stats used to compute the weighting
        self.descriptor_corr = pick_uncorrelated_columns(self.df,
                                                         self.decriptor_stats,
                                                         r2_cutoff=self.r2_cutoff,
                                                         resort=False)
        # Add descriptor weights to the self.descriptor_stats DataFrame
        calculate_descriptor_weights(self.decriptor_stats)

    def get_pMPO_statistics(self) -> pd.DataFrame:
        """
        Get the pMPO statistics calculated on all the descriptors
        :return: A DataFrame with the pMPO model statistics
        """
        return self.decriptor_stats

    def get_descriptor_correlation(self) -> pd.DataFrame:
        """
        Get the correlation matrix for all input descriptors
        :return: A DataFrame with a NxN correlation matrix
        """
        return self.descriptor_corr

    def get_pMPO(self):
        """
        Return a simple pMPO model that can be reused for scoring
        :return:
        """
        # TODO: This is currently set up for sigmoidal functions only (as is the rest of the pMPO)
        # It could be extended to different functional forms
        if self.pMPO is None:
            self.pMPO = pMPOModel(self.pMPO_model_name)
            for row in self.decriptor_stats[(self.decriptor_stats['selected'] == True)][['name', 'w', 'good_mean', 'good_std']].iterrows():  # noqa
                row_dict = row[1].to_dict()
                # Rename the columns
                row_dict['mean'] = row_dict.pop('good_mean')
                row_dict['std'] = row_dict.pop('good_std')
                row_dict['weight'] = row_dict.pop('w')
                # Create the function
                fn = SigmoidalFunction(**row_dict)
                self.pMPO.register(row_dict['name'], fn)
            return self.pMPO
        else:
            return self.pMPO