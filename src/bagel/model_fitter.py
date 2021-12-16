"""
.. module:: model_fitter
    :platform: Unix, Mac, Windows
    :synopsis: Microlensing model fitter.

.. moduleauthor:: Jessica Lu <jlu.astro@berkeley.edu>
.. moduleauthor:: Michael Medford <MichaelMedford@berkeley.edu>
.. moduleauthor:: Casey Lam <casey_lam@berkeley.edu>
.. moduleauthor:: Edward Broadberry

"""

from pymultinest.solve import Solver
import os
from astropy.table.row import Row
import glob
import math
import numpy as np
import pylab as plt
import scipy.stats
import pymultinest
import src.micromodel.model as mmodel 
from astropy.table import Table
from astropy.table import Row
from astropy import units
from astropy.stats import sigma_clipped_stats
import json
from string import digits
import copy
import pdb
from datetime import date
import yaml

from dynesty import plotting as dyplot
from six.moves import range
import matplotlib.patches as mpatches

import logging
import types
from matplotlib.ticker import MaxNLocator, NullLocator
from matplotlib.colors import LinearSegmentedColormap, colorConverter
from matplotlib.ticker import ScalarFormatter
from scipy import spatial
from scipy.ndimage import gaussian_filter as norm_kde
from scipy.stats import gaussian_kde
import warnings
from dynesty.utils import resample_equal, unitcheck
from dynesty.utils import quantile as _quantile
import re

try:
    str_type = types.StringTypes
    float_type = types.FloatType
    int_type = types.IntType
except:
    str_type = str
    float_type = float
    int_type = int

muS_scale_factor = 100.0

class PSPL_Solver(Solver):
    """
    A PyMultiNest solver to find the optimal PSPL parameters, given data and
    a microlensing model from model.py.
    DESPITE THE NAME YOU CAN ALSO USE IT TO FIT PSBL! 

    Inputs
    ------
    data : dictionary
        Observational data used to fit a microlensing model. What the data must
        contain depends on what type of microlensing model you are solving for.

        The data dictionary must always photometry information of at least one
        filter. This data must contain the times, magnitudes, and magnitude
        errors of the observations. The keys to these arrays are:
            `t_phot1` (MJD)
            `mag1` (magnitudes)
            `mag_err1` (magnitudes)

        PSPL_Solver supports multiple photometric filters. For each
        additional filter, increments the extension of the above keys by one.
        For example, a second filter would be:
            `t_phot2` (MJD)
            `mag2` (magnitudes)
            `mag_err2` (magnitudes)

        PSPL_Solver supports solving microlensing models that calculate with
        parallax. These models must be accompanied with data that contains the
        right ascenscion and declination of the target. These keys are:
            `raL` (decimal degrees)
            `decL` (decimal degrees)

        PSPL_Solver supports solving microlensing models that fit astrometry.
        These models must be accompanied with data that contains astrometric
        observations in the following keys:
            `t_ast` (MJD)
            `xpos` (arcsec along East-West increasing to the East)
            `ypos` (arcsec along the North-South increasing to the North)
            `xpos_err` (arcsec)
            `ypos_err` (arcsec)

    model_class :
        PSPL_Solver must be provided with the microlensing model that you are
        trying to fit to your data. These models are written out in model.py,
        along with extensive documentation as to their content and
        construction in the file's docstring. The model can support either
        (1) photometric data or photometric and astrometric data,
        (2) parallax or no parallax, and
        (3) different parameterizations of the model.

        For example, a model with accepts both astrometric and photometric
        data, uses parallax, and uses a parameterization that includes the
        distance to the source and the lens is: `PSPL_PhotAstrom_Par_Param1`.

    Optional Inputs
    ---------------
    custom_additional_param_names : list
        If provided, the fitter will override the default
        `additional_param_names` of the model_class. These are the parameters,
        besides those that are being fitted for, that are written out to disk
        for posterior plotting after the fit has completed. To see the default
        additional_param_names run:
            `print(model_class.additional _param_names)`
    add_error_on_photometry : boolean
        If set to True, the fitter will fit for an additive error to the
        photometric magnitudes in the fitting process. This error will have
        the name `add_errN`, with an `N` equal to the filter number.
    multiply_error_on_photometry : boolean
        If set to True, the fitter will fit for a multiplicative error to the
        photometric magnitudes in the fitting process. This error will have
        the name `mult_errN`, with an `N` equal to the filter number.

    All other parameters :
        See pymultinest.run() for a description of all other parameters.

    Example Declaration
    -------------------

    Assuming that a data dictionary has been instantiated with the above keys,
    and that a model has been loaded in from model.py, PSPL_Solver can be run
    with the following commands:

        fitter = PSPL_Solver(data,
                             PSPL_PhotAstrom_Par_Param1,
                             add_error_on_photometry=True,
                             custom_additional_param_names=['dS', 'tE'],
                             outputfiles_basename='./model_output/test_')
        fitter.solve()


    
    """

    default_priors = {
        'mL': ('make_gen', 0, 100),
        't0': ('make_t0_gen', None, None),
        'xS0_E': ('make_xS0_gen', None, None),
        'xS0_N': ('make_xS0_gen', None, None),
        'u0_amp': ('make_gen', -1, 1),
        'beta': ('make_gen', -2, 2),
        'muL_E': ('make_gen', -20, 20),
        'muL_N': ('make_gen', -20, 20),
        'muS_E': ('make_muS_EN_gen', None, None),
        'muS_N': ('make_muS_EN_gen', None, None),
        'dL': ('make_gen', 1000, 8000),
        'dS': ('make_gen', 100, 10000),
        'dL_dS': ('make_gen', 0.01, 0.99),
        'b_sff': ('make_gen', 0.0, 1.5),
        'mag_src': ('make_mag_src_gen', None, None),
        'mag_base': ('make_mag_base_gen', None, None),
        'tE': ('make_gen', 1, 400),
        'piE_E': ('make_gen', -1, 1),
        'piE_N': ('make_gen', -1, 1),
        'thetaE': ('make_lognorm_gen', 0, 1),
        'log10_thetaE': ('make_truncnorm_gen', -0.2, 0.3, -4, 4),
        'q': ('make_gen', 0.001, 1),
        'alpha': ('make_gen', 0, 360),
        'phi': ('make_gen', 0, 360),
        'sep': ('make_gen', 1e-4, 2e-2),
        'piS': ('make_piS', None, None),
        'add_err': ('make_gen', 0, 0.3),
        'mult_err': ('make_gen', 1.0, 3.0),
        'radius': ('make_gen', 1E-4, 1E-2),
        # We really need to make some normal distributions. All these are junk right now.
        'gp_log_rho': ('make_norm_gen', 0, 5),
        'gp_log_S0': ('make_norm_gen', 0, 5),
        'gp_log_sigma': ('make_norm_gen', 0, 5), 
        'gp_rho':('make_invgamma_gen', None, None),
        'gp_log_omega04_S0':('make_norm_gen', 0, 5), # FIX... get from data
        'gp_log_omega0':('make_norm_gen', 0, 5)
    }

    def __init__(self, data, model_class,
                 custom_additional_param_names=None,
                 add_error_on_photometry=False,
                 multiply_error_on_photometry=False,
                 use_phot_optional_params=True,
                 use_ast_optional_params=True,
                 wrapped_params=None,
                 importance_nested_sampling=False,
                 multimodal=True, const_efficiency_mode=False,
                 n_live_points=300,
                 evidence_tolerance=0.5, sampling_efficiency=0.8,
                 n_iter_before_update=100, null_log_evidence=-1e90,
                 max_modes=100, mode_tolerance=-1e90,
                 outputfiles_basename="chains/1-", seed=-1, verbose=False,
                 resume=False, context=0, write_output=True, log_zero=-1e100,
                 max_iter=0, init_MPI=False, dump_callback=None):
        """
        Accepted optional inputs are the same as on pymultinest.run().

        Note that prior distributions are defined upon initiatlization and
        can be modified on the object before running solve().

        Optional Inputs
        ---------------

        use_phot_optional_params : bool, or list of bools
        
        
        """

        # Set the data, model, and error modes
        self.data = data
        self.model_class = model_class
        self.add_error_on_photometry = add_error_on_photometry
        self.multiply_error_on_photometry = multiply_error_on_photometry
        self.use_phot_optional_params = use_phot_optional_params
        self.use_ast_optional_params = use_ast_optional_params

        # Check the data
        self.check_data()

        # list of all possible multi-filt, multi-phot, multi-ast parameters that anyone
        # could ever possibly use.
        self.multi_filt_params = ['b_sff', 'mag_src', 'mag_base', 'add_err', 'mult_err',
                                  'gp_log_sigma', 'gp_log_rho', 'gp_log_S0', 'gp_log_omega0', 'gp_rho',
                                  'gp_log_omega04_S0', 'gp_log_omega0', 'add_err', 'mult_err']

        self.gp_params = ['gp_log_sigma', 'gp_log_rho', 'gp_log_S0', 'gp_log_omega0', 'gp_rho',
                          'gp_log_omega04_S0', 'gp_log_omega0']

        # Set up parameterization of the model
        self.remove_digits = str.maketrans('', '', digits)  # removes nums from strings
        self.custom_additional_param_names = custom_additional_param_names
        self.n_phot_sets = None
        self.n_ast_sets = None
        self.fitter_param_names = None
        self.additional_param_names = None
        self.all_param_names = None
        self.n_dims = None
        self.n_params = None
        self.n_clustering_params = None
        self.setup_params()

        # Set multinest stuff
        self.multimodal = multimodal
        self.wrapped_params = wrapped_params
        self.importance_nested_sampling = importance_nested_sampling
        self.const_efficiency_mode = const_efficiency_mode
        self.n_live_points = n_live_points
        self.evidence_tolerance = evidence_tolerance
        self.sampling_efficiency = sampling_efficiency
        self.n_iter_before_update = n_iter_before_update
        self.null_log_evidence = null_log_evidence
        self.max_modes = max_modes
        self.mode_tolerance = mode_tolerance
        self.outputfiles_basename = outputfiles_basename
        self.seed = seed
        self.verbose = verbose
        self.resume = resume
        self.context = context
        self.write_output = write_output
        self.log_zero = log_zero
        self.max_iter = max_iter
        self.init_MPI = init_MPI
        self.dump_callback = dump_callback

        # Setup the default priors
        self.priors = None
        self.make_default_priors()

        # Stuff needed for using multinest posteriors as priors.
        self.post_param_cdf = None
        self.post_param_names = None
        self.post_param_bininds = None
        self.post_param_bins = None

        # Make the output directory if doesn't exist
        if os.path.dirname(outputfiles_basename) != '':
            os.makedirs(os.path.dirname(outputfiles_basename), exist_ok=True)

        return

    def check_data(self):
        if 't_ast1' in self.data.keys():
            if not self.model_class.paramAstromFlag or \
                    not self.model_class.astrometryFlag:
                print('***** WARNING: ASTROMETRY DATA WILL NOT BE FIT '
                      'BY %s *****' % str(self.model_class))
        else:
            if self.model_class.paramAstromFlag or \
                    self.model_class.astrometryFlag:
                raise RuntimeError('Astrometry data required to '
                                   'run %s' % str(self.model_class))

        if 't_phot1' in self.data.keys():
            if not self.model_class.paramPhotFlag or \
                    not self.model_class.photometryFlag:
                print('***** WARNING: PHOTOMETRY DATA WILL NOT BE FIT '
                      'BY %s *****' % str(self.model_class))
        else:
            if self.model_class.paramPhotFlag or \
                    self.model_class.photometryFlag:
                raise RuntimeError('Photometry data required to '
                                   'run %s' % str(self.model_class))

    def setup_params(self):
        # Number of photometry sets
        n_phot_sets = 0
        # Number of astrometry sets
        n_ast_sets = 0

        phot_params = []
        ast_params = []

        # The indices in map_phot_idx_to_ast_idx map phot to astrom
        # map_phot_idx_to_ast_idx <--> [0, 1, 2, ... len(map_phot_idx_to_ast_idx)-1]
        map_phot_idx_to_ast_idx = []

        for key in self.data.keys():
            if 't_phot' in key and (self.model_class.paramPhotFlag or self.model_class.photometryFlag):
                n_phot_sets += 1

                # Photometry parameters
                for phot_name in self.model_class.phot_param_names:
                    phot_params.append(phot_name + str(n_phot_sets))

                # Optional photometric parameters -- not all filters
                for opt_phot_name in self.model_class.phot_optional_param_names:
                    if isinstance(self.use_phot_optional_params, (list, np.ndarray)):
                        if self.use_phot_optional_params[n_phot_sets-1]:
                            phot_params.append(opt_phot_name + str(n_phot_sets))
                    # Case: single value -- set for all filters. 
                    else:
                        if self.use_phot_optional_params:
                            phot_params.append(opt_phot_name + str(n_phot_sets))
                        else:
                            msg = 'WARNING: Your model supports optional photometric parameters; '
                            msg += 'but you have disabled them for all filters. '
                            msg += 'Consider using a simpler model instead.'
                            print(msg)
                            
                # Additive error parameters (not on the model) -- not all filters
                if self.add_error_on_photometry:
                    # Case: List -- control additive error on each filter.
                    if isinstance(self.add_error_on_photometry, (list, np.ndarray)):
                        if self.add_error_on_photometry[n_phot_sets-1]:
                            phot_params.append('add_err' + str(n_phot_sets))
                    # Case: single value -- set for all filters. 
                    else:
                        phot_params.append('add_err' + str(n_phot_sets))
                    
                # Multiplicative error parameters (not on the model) -- not all filters
                if self.multiply_error_on_photometry:
                    # Case: List -- control additive error on each filter.
                    if isinstance(self.multiply_error_on_photometry, (list, np.ndarray)):
                        if self.multiply_error_on_photometry[n_phot_sets-1]:
                            phot_params.append('mult_err' + str(n_phot_sets))
                    # Case: single value -- set for all filters. 
                    else:
                        phot_params.append('mult_err' + str(n_phot_sets))

            if 't_ast' in key and (self.model_class.paramAstromFlag or self.model_class.astrometryFlag):
                n_ast_sets += 1

                # Optional astrometric parameters -- not all filters
                for opt_ast_name in self.model_class.ast_optional_param_names:
                    if isinstance(self.use_ast_optional_params, (list, np.ndarray)):
                        if self.use_ast_optional_params[n_ast_sets-1]:
                            ast_params.append(opt_ast_name + str(n_ast_sets))
                    # Case: single value -- set for all filters. 
                    else:
                        if self.use_ast_optional_params:
                            ast_params.append(opt_ast_name + str(n_ast_sets))
                        else:
                            msg = 'WARNING: Your model supports optional astrometric parameters; '
                            msg += 'but you have disabled them for all filters. '
                            msg += 'Consider using a simpler model instead.'
                            print(msg)
                
        # The indices in map_phot_idx_to_ast_idx map phot to astrom
        # map_phot_idx_to_ast_idx <--> [0, 1, 2, ... len(map_phot_idx_to_ast_idx)-1]
        if n_ast_sets > 0 and n_phot_sets > 0:
            for aa in self.data['ast_data']:
                try:
                    idx = self.data['phot_data'].index(aa)
                    map_phot_idx_to_ast_idx.append(idx)
                except ValueError:
                    print('*** CHECK YOUR INPUT! All astrometry data must have a corresponding photometry data set! ***')
                    raise

        self.n_phot_sets = n_phot_sets
        self.n_ast_sets = n_ast_sets
        self.map_phot_idx_to_ast_idx = map_phot_idx_to_ast_idx
        self.fitter_param_names = self.model_class.fitter_param_names + \
                                  phot_params + ast_params

        if self.custom_additional_param_names is not None:
            self.additional_param_names = []
            for cc, param_name in enumerate(self.custom_additional_param_names):
                if param_name in self.multi_filt_params:
                    # Special handling for gp params 
                    if param_name in self.gp_params:
                        if self.use_phot_optional_params is True:
                            for ff in range(n_phot_sets):
                                self.additional_param_names += [param_name + str(ff+1)]
                        elif self.use_phot_optional_params is False:
                            continue
                        else:
                            for ii, use in enumerate(self.use_phot_optional_params):
                                if use:
                                    self.additional_param_names += [param_name + str(ii+1)]
                else:
                    self.additional_param_names += [param_name]

        else:
            self.additional_param_names = []
            for i, param_name in enumerate(self.model_class.additional_param_names):
                if param_name in self.multi_filt_params:  
                    # Special handling for gp params 
                    if param_name in self.gp_params:
                        if self.use_phot_optional_params is True:
                            for nn in range(self.n_phot_sets):
                                self.additional_param_names += [param_name + str(nn+1)]
                        elif self.use_phot_optional_params is False:
                            continue
                        else:
                            for ii, use in enumerate(self.use_phot_optional_params):
                                if use:
                                    self.additional_param_names += [param_name + str(ii+1)]
                else:
                    self.additional_param_names += [param_name]

        self.all_param_names = self.fitter_param_names + self.additional_param_names

        self.n_dims = len(self.fitter_param_names)
        self.n_params = len(self.all_param_names)  # cube dimensions
        self.n_clustering_params = self.n_dims

    def make_default_priors(self):
        """
        Setup our prior distributions (i.e. random samplers). We will
        draw from these in the Prior() function. We set them up in advance
        because they depend on properties of the data. Also,
        they can be over-written by custom priors as desired.

        To make your own custom priors, use the make_gen() functions
        with different limits.
        """
#        if os.path.exists("u0.txt"):
#            os.remove("u0.txt")
#
#        if os.path.exists("piEE.txt"):
#            os.remove("piEE.txt")
#
#        if os.path.exists("piEN.txt"):
#            os.remove("piEN.txt")

        self.priors = {}
        for param_name in self.fitter_param_names:
            if any(x in param_name for x in self.multi_filt_params):
                priors_name, filt_index = split_param_filter_index1(param_name)
            else:
                priors_name = param_name
                filt_index = None
                
            # FIXME: can we write the code so it doesn't require the prior to exist here?
            foo = self.default_priors[priors_name]
            prior_type = foo[0]
            if prior_type == 'make_gen':
                prior_min = foo[1]
                prior_max = foo[2]
                self.priors[param_name] = make_gen(prior_min, prior_max)

            if prior_type == 'make_norm_gen':
                prior_mean = foo[1]
                prior_std = foo[2]
                self.priors[param_name] = make_norm_gen(prior_mean, prior_std)

            if prior_type == 'make_lognorm_gen':
                prior_mean = foo[1]
                prior_std = foo[2]
                self.priors[param_name] = make_lognorm_gen(prior_mean, prior_std)

            if prior_type == 'make_truncnorm_gen':
                prior_mean = foo[1] 
                prior_std = foo[2]
                prior_lo_cut = foo[3]
                prior_hi_cut = foo[4]
                self.priors[param_name] = make_truncnorm_gen(prior_mean, prior_std, prior_lo_cut, prior_hi_cut)

            if prior_type == 'make_invgamma_gen':
                n_digits = len(param_name) - len(priors_name)
                # Get the right indices. 
                num = int(param_name[-n_digits:])
                self.priors[param_name] = make_invgamma_gen(self.data['t_phot' + str(num)])

            elif prior_type == 'make_t0_gen':
                # Hard-coded to use the first data set to set the t0 prior.
                self.priors[param_name] = make_t0_gen(self.data['t_phot1'],
                                                      self.data['mag1'])

            elif prior_type == 'make_xS0_gen':

                if param_name == 'xS0_E':
                    pos = self.data['xpos1']

                elif param_name == 'xS0_N':
                    pos = self.data['ypos1']

                self.priors[param_name] = make_xS0_gen(pos)

            elif prior_type == 'make_muS_EN_gen':

                if param_name == 'muS_E':
                    pos = self.data['xpos1']

                elif param_name == 'muS_N':
                    pos = self.data['ypos1']

                self.priors[param_name] = make_muS_EN_gen(self.data['t_ast1'],
                                                          pos,
                                                          scale_factor=muS_scale_factor)
            elif prior_type == 'make_piS':
                self.priors[param_name] = make_piS()

            elif prior_type == 'make_fdfdt':
                self.priors[param_name] = make_fdfdt()

            elif prior_type == 'make_mag_base_gen':
                self.priors[param_name] = make_mag_base_gen(self.data['mag' + str(filt_index)])
                

        return

    def get_model(self, params):
        if self.model_class.parallaxFlag:
            raL, decL = self.data['raL'], self.data['decL']
        else:
            raL, decL = None, None
        params_dict = generate_params_dict(params,
                                           self.fitter_param_names)
        
        mod = self.model_class(*params_dict.values(),
                                 raL=raL,
                                 decL=decL)

        # FIXME: Why are we updating params here???
        if not isinstance(params, (dict, Row)):
            # FIXME: is there abetter way to do this.
            for i, param_name in enumerate(self.additional_param_names):
                filt_name, filt_idx = split_param_filter_index1(param_name)

                if filt_idx == None:   # Not a multi-filter paramter.
                    params[self.n_dims + i] = getattr(mod, param_name)
                else:
                    params[self.n_dims + i] = getattr(mod, filt_name)[filt_idx-1]

        return mod

    # FIXME: Is there a reason Prior takes ndim and nparams when those aren't used?
    # Is it the same reason as LogLikelihood?
    def Prior(self, cube, ndim=None, nparams=None):
        for i, param_name in enumerate(self.fitter_param_names):
            cube[i] = self.priors[param_name].ppf(cube[i])
        return cube

    def Prior_copy(self, cube):
        cube_copy = cube.copy()
        for i, param_name in enumerate(self.fitter_param_names):
            cube_copy[i] = self.priors[param_name].ppf(cube[i])

        # Append on additional parameters.
        add_params = np.zeros(len(self.additional_param_names), dtype='float')
        cube_copy = np.append(cube_copy, add_params)
        # Strangely, get_model does the parameter updating for the additional parameters.
        # This should really be elsewhere FIXME.
        model = self.get_model(cube_copy)  
            
        return cube_copy
    
    # FIXME: I pass in ndim and nparams since that's what's done in Prior, but I don't think they're necessary?
    def Prior_from_post(self, cube, ndim=None, nparams=None):
        # Get the bin midpoints
        binmids = []
        for bb in np.arange(len(self.post_param_bins)):
            binmids.append((self.post_param_bins[bb][:-1] + self.post_param_bins[bb][1:])/2)

        # Draw a random sample from the posteriors.
        post_params = self.sample_post(binmids, self.post_param_cdf, self.post_param_bininds) 

        # Make the cube by combining the posterior draws and the 1-D priors.
        for i, param_name in enumerate(self.fitter_param_names):
            if param_name in self.post_param_names:
                pdx = self.post_param_names.index(param_name)
                cube[i] = post_params[pdx]
            else:
                cube[i] = self.priors[param_name].ppf(cube[i])

        return cube

    def sample_post(self, binmids, cdf, bininds):  
        """
        Randomly sample from a multinest posterior distribution.

        Parameters
        ----------
        Nparams = number of parameters
        Nbins = number of histogram bins per dimension
        Nnzero = number of histogram bins with non-zero probability

        binmids : list of length N, each list entry is an array of shape (M, )
            The centers of the bins for each parameter
        cdf : (Nnzero, ) array
            CDF of the distribution. Only the non-zero probability entries.
        bininds : (Nnzero, Nparams) array
            Histogram indices of the non-zero probability entries.
        """
        # Make a random sample from the posterior using inverse transform sampling.
        rr = np.random.uniform()
        if len(np.where(cdf > rr)[0]) == 0:
            idx = 0
        else:
            idx = np.min(np.where(cdf > rr)[0])

        # Get the random sample.
        Npars = len(bininds[0])
        pars = np.empty(len(bininds[0]), dtype=float)
        for i in range(Npars):
            pars[i] = binmids[i][int(bininds[idx,i])]
            # Sample randomly within the bin width, so not just discreet points.
            pars[i] += np.random.uniform() * (binmids[i][1] - binmids[i][0]) 

        return pars 

    def LogLikelihood(self, cube, ndim=None, n_params=None):
        """
        This is just a wrapper because PyMultinest requires passing in
        the ndim and nparams.
        """
        lnL = self.log_likely(cube, verbose=self.verbose)
#        lnL = self.log_likely0(cube, verbose=self.verbose)

        return lnL
    
    def dyn_prior(self, cube):
        for i, param_name in enumerate(self.fitter_param_names):
            cube[i] = self.priors[param_name].ppf(cube[i])

        return cube

    def dyn_log_likely(self, cube):
        lnL = self.log_likely(cube, verbose=self.verbose)

        return lnL
        
    def log_likely_astrometry(self, model):
        if model.astrometryFlag:
            lnL_ast = 0.0
            
            # If no photometry
            if len(self.map_phot_idx_to_ast_idx) == 0:
                for i in range(self.n_ast_sets):
                    lnL_ast_i = model.log_likely_astrometry(self.data['t_ast' + str(i+1)],
                                                            self.data['xpos' + str(i+1)],
                                                            self.data['ypos' + str(i+1)],
                                                            self.data['xpos_err' + str(i+1)],
                                                            self.data['ypos_err' + str(i+1)],
                                                            ast_filt_idx = i)
                    lnL_ast += lnL_ast_i.sum() 

            # If photometry
            else:
                for i in range(self.n_ast_sets):
                    lnL_ast_i = model.log_likely_astrometry(self.data['t_ast' + str(i+1)],
                                                            self.data['xpos' + str(i+1)],
                                                            self.data['ypos' + str(i+1)],
                                                            self.data['xpos_err' + str(i+1)],
                                                            self.data['ypos_err' + str(i+1)],
                                                            ast_filt_idx = self.map_phot_idx_to_ast_idx[i])
                    lnL_ast += lnL_ast_i.sum() 
        else:
            lnL_ast = 0

        return lnL_ast

    def log_likely_photometry(self, model, cube):
        if model.photometryFlag:
            lnL_phot = 0.0

            for i in range(self.n_phot_sets):
                t_phot = self.data['t_phot' + str(i + 1)]
                mag = self.data['mag' + str(i + 1)]
                
                # additive or multiplicative error
                mag_err = self.get_modified_mag_err(cube, i)
                
                lnL_phot += model.log_likely_photometry(t_phot, mag, mag_err, i)
        else:
            lnL_phot = 0

        return lnL_phot
        

    def log_likely(self, cube, verbose=False):
        """
        cube : list or dict
            The dictionary or cube of the model parameters.
        """
        model = self.get_model(cube)
            
        lnL_phot = self.log_likely_photometry(model, cube)
        lnL_ast = self.log_likely_astrometry(model)

        lnL = lnL_phot + lnL_ast
            
        if verbose:
            # self.plot_model_and_data(model)
            # pdb.set_trace()
            
            fmt = '{0:13s} = {1:f} '
            for ff in range(self.n_params):
                if isinstance(cube, dict) or isinstance(cube, Row):
                    pname = self.all_param_names[ff]
                    if ((isinstance(cube, dict) and pname in cube) or
                        (isinstance(cube, Row)  and pname in cube.colnames)):
                        print(fmt.format(pname, cube[pname])),
                    else:
                        print(fmt.format(pname, -999.0)),
                else:
                    print(fmt.format(self.all_param_names[ff], cube[ff])),
            print(fmt.format('lnL_phot', lnL_phot)),
            print(fmt.format('lnL_ast', lnL_ast)),
            print(fmt.format('lnL', lnL))

        return lnL

# Code for randomly sampling prior
#    def log_likely0(self, cube, verbose=False):
#        """
#        cube : list or dict
#            The dictionary or cube of the model parameters.
#        """
#        model = self.get_model(cube)
#
#        with open("u0.txt", "a") as f:
#            t = cube[1]
#            f.write(str(t) + '\n')
#
#        with open("piEE.txt", "a") as f:
#            t = cube[5]
#            f.write(str(t) + '\n')
#
#        with open("piEN.txt", "a") as f:
#            t = cube[6]
#            f.write(str(t) + '\n')
#            
#        return -1


    def get_modified_mag_err(self, cube, filt_index):
        mag_err = copy.deepcopy(self.data['mag_err' + str(filt_index + 1)])

        if self.add_error_on_photometry:
            add_err_name = 'add_err' + str(filt_index + 1)
            if isinstance(cube, dict) or isinstance(cube, Row):
                add_err = cube[add_err_name]
            else:
                add_err_idx = self.all_param_names.index(add_err_name)
                add_err = cube[add_err_idx]
            mag_err = np.hypot(mag_err, add_err)

        if self.multiply_error_on_photometry:
            mult_err_name = 'mult_err' + str(filt_index + 1)
            if isinstance(cube, dict) or isinstance(cube, Row):
                mult_err = cube[mult_err_name]
            else:
                mult_err_idx = self.all_param_names.index(mult_err_name)
                mult_err = cube[mult_err_idx]
            mag_err *= mult_err

        return mag_err        


    def write_params_yaml(self):
        """
        Write a YAML file that contains the parameters to re-initialize
        this object, if desired. 
        """
        params = {}

        params['target'] = self.data['target']
        params['phot_data'] = self.data['phot_data']
        params['phot_files'] = self.data['phot_files']
        params['astrom_data'] = self.data['ast_data']
        params['astrom_files'] = self.data['ast_files']
        params['add_error_on_photometry'] = self.add_error_on_photometry
        params['multiply_error_on_photometry'] = self.multiply_error_on_photometry
        params['use_phot_optional_params'] = self.use_phot_optional_params
        params['use_ast_optional_params'] = self.use_ast_optional_params
        
        params['model'] = self.model_class.__name__
        params['custom_additional_param_names'] = self.custom_additional_param_names
        params['wrapped_params'] = self.wrapped_params
        params['run_date'] = str(date.today())

        with open(self.outputfiles_basename + 'params.yaml', 'w') as f:
            foo = yaml.dump(params, f)
        
        return

    def solve(self):
        """
        Run a MultiNest fit to find the optimal parameters (and their
        posteriors) given the data.

        Note we will ALWAYS tell multinest to be verbose.
        """
        self.write_params_yaml()

        # Choose whether to use self.Prior or self.Prior_from_post depending
        # on whether self.post_param_names is none or not.
        use_prior = None
        if self.post_param_cdf is not None:
            use_prior = self.Prior_from_post
        else:
            use_prior = self.Prior
        print('*************************************************')
        print('*** Using', use_prior.__name__, 'for prior function. ***')
        print('*************************************************')

        pymultinest.run(self.LogLikelihood, use_prior, self.n_dims,
                        n_params=self.n_params,
                        n_clustering_params=self.n_clustering_params,
                        multimodal=self.multimodal,
                        importance_nested_sampling=self.importance_nested_sampling,
                        wrapped_params=self.wrapped_params,
                        const_efficiency_mode=self.const_efficiency_mode,
                        n_live_points=self.n_live_points,
                        evidence_tolerance=self.evidence_tolerance,
                        sampling_efficiency=self.sampling_efficiency,
                        n_iter_before_update=self.n_iter_before_update,
                        null_log_evidence=self.null_log_evidence,
                        max_modes=self.max_modes,
                        mode_tolerance=self.mode_tolerance,
                        outputfiles_basename=self.outputfiles_basename,
                        seed=self.seed,
                        # verbose=self.verbose,
                        verbose=True,
                        resume=self.resume,
                        context=self.context,
                        write_output=self.write_output,
                        log_zero=self.log_zero,
                        max_iter=self.max_iter,
                        init_MPI=self.init_MPI,
                        dump_callback=self.dump_callback)

        return

    def separate_modes(self):
        """
        Reads in the fits for the different modes (post_separate.dat)
        and splits it into a .dat file per mode.

        Is there a more intelligent way to deal with all the indices???
        Write better later, but it seems to work for now...
        """
        mode_file = self.outputfiles_basename + 'post_separate.dat'

        # Search for the empty lines (these separate the different modes)
        empty_lines = []
        with open(mode_file, 'r') as orig_file:
            for num, line in enumerate(orig_file, start=0):
                if line == '\n':
                    empty_lines.append(num)

        # Error checking
        if len(empty_lines) % 2 != 0:
            print('SOMETHING BAD HAPPENED!')

        # Figure out how many modes there are (# modes = idx_range)
        idx_range = int(len(empty_lines) / 2)

        # Split into the different files
        orig_tab = np.loadtxt(mode_file)
        for idx in np.arange(idx_range):
            start_idx = empty_lines[idx * 2 + 1] + 1 - 2 * (idx + 1)
            if idx != np.arange(idx_range)[-1]:
                end_idx = empty_lines[idx * 2 + 2] - 2 * (idx + 1)
                np.savetxt(
                    self.outputfiles_basename + 'mode' + str(idx) + '.dat',
                    orig_tab[start_idx:end_idx])
            else:
                np.savetxt(
                    self.outputfiles_basename + 'mode' + str(idx) + '.dat',
                    orig_tab[start_idx:])

        return

    def calc_best_fit(self, tab, smy, s_idx=0, def_best='maxl'):
        """
        Returns best-fit parameters, where best-fit can be
        median, maxl, or MAP. Default is maxl.

        If best-fit is median, then also return +/- 1 sigma
        uncertainties.

        If best-fit is MAP, then also need to indicate which row of
        summary table to use. Default is s_idx = 0 (global solution).
        s_idx = 1, 2, ... , n for the n different modes.

        tab = self.load_mnest_results()
        smy = self.load_mnest_summary()
        """

        params = self.all_param_names

        # Use Maximum Likelihood solution
        if def_best.lower() == 'maxl':
            best = np.argmax(tab['logLike'])
            tab_best = tab[best][params]

            return tab_best

        # Use MAP solution
        if def_best.lower() == 'map':
            # tab_best = {}
            # for n in params:
            #     if (n != 'weights' and n != 'logLike'):
            #         tab_best[n] = smy['MAP_' + n][s_idx]
            
            # Recalculate ourselves. No dependence on smy.
            best = np.argmax(tab['weights'])
            tab_best = tab[best][params]

            return tab_best

        # Use mean solution
        if def_best.lower() == 'mean':
            tab_best = {}
            tab_errors = {}
            
            for n in params:
                if (n != 'weights' and n != 'logLike'):
                    tab_best[n] = np.mean(tab[n])
                    tab_errors[n] = np.std(tab[n])
                    
            return tab_best, tab_errors

        # Use median solution
        if def_best.lower() == 'median':
            tab_best = {}
            med_errors = {}
            sumweights = np.sum(tab['weights'])
            weights = tab['weights'] / sumweights

            sig1 = 0.682689
            sig2 = 0.9545
            sig3 = 0.9973
            sig1_lo = (1. - sig1) / 2.
            sig2_lo = (1. - sig2) / 2.
            sig3_lo = (1. - sig3) / 2.
            sig1_hi = 1. - sig1_lo
            sig2_hi = 1. - sig2_lo
            sig3_hi = 1. - sig3_lo

            for n in params:
                # Calculate median, 1 sigma lo, and 1 sigma hi credible interval.
                tmp = weighted_quantile(tab[n], [0.5, sig1_lo, sig1_hi],
                                        sample_weight=weights)
                tab_best[n] = tmp[0]

                # Switch from values to errors.
                err_lo = tmp[0] - tmp[1]
                err_hi = tmp[2] - tmp[0]

                med_errors[n] = np.array([err_lo, err_hi])

            return tab_best, med_errors

    def get_best_fit(self, def_best='maxl'):
        """
        Returns best-fit parameters, where best-fit can be
        median, maxl, or MAP. Default is maxl.

        If best-fit is median, then also return +/- 1 sigma
        uncertainties.

        tab = self.load_mnest_results()
        smy = self.load_mnest_summary()
        """
        tab = self.load_mnest_results()
        smy = self.load_mnest_summary()

        best_fit = self.calc_best_fit(tab=tab, smy=smy, s_idx=0,
                                      def_best=def_best)

        return best_fit

    def get_best_fit_modes(self, def_best='maxl'):
        """Identify best-fit model
        """
        tab_list = self.load_mnest_modes()
        smy = self.load_mnest_summary()

        best_fit_list = []

        # ADD A USEFUL COMMENT HERE ABOUT INDEXING!!!!!!
        for ii, tab in enumerate(tab_list, 1):
            best_fit = self.calc_best_fit(tab=tab, smy=smy, s_idx=ii,
                                          def_best=def_best)
            best_fit_list.append(best_fit[0])

        return best_fit_list

    def get_best_fit_model(self, def_best='maxl'):
        """
        Identify best-fit model

        def_best : str
            Choices are 'map' (maximum a posteriori), 'median', or
            'maxl' (maximum likelihood)

        """
        best = self.get_best_fit(def_best=def_best)
        if ((def_best == 'median') or (def_best == 'mean')):
            pspl_mod = self.get_model(best[0])
        else:
            pspl_mod = self.get_model(best)
        return pspl_mod

    def get_best_fit_modes_model(self, def_best='maxl'):
        best_list = self.get_best_fit_modes(def_best=def_best)
        pspl_mod_list = []

        for best in best_list:
            pspl_mod = self.get_model(best)
            pspl_mod_list.append(pspl_mod)

        return pspl_mod_list

    def load_mnest_results(self, remake_fits=False):
        """Load up the MultiNest results into an astropy table.
        """
        outroot = self.outputfiles_basename

        if remake_fits or not os.path.exists(outroot + '.fits'):
            # Load from text file (and make fits file)
            tab = Table.read(outroot + '.txt', format='ascii')

            # Convert to log(likelihood) since Multinest records -2*logLikelihood
            tab['col2'] /= -2.0

            # Rename the parameter columns. This is hard-coded to match the
            # above run() function.
            tab.rename_column('col1', 'weights')
            tab.rename_column('col2', 'logLike')

            for ff in range(len(self.all_param_names)):
                cc = 3 + ff
                tab.rename_column('col{0:d}'.format(cc), self.all_param_names[ff])

            tab.write(outroot + '.fits', overwrite=True)
        else:
            # Load much faster from fits file.
            tab = Table.read(outroot + '.fits')

        return tab

    def load_mnest_summary(self, remake_fits=False):
        """
        Load up the MultiNest results into an astropy table.
        """
        sum_root = self.outputfiles_basename + 'summary'

        if remake_fits or not os.path.exists(sum_root + '.fits'):
            # Load from text file (and make fits file)
            tab = Table.read(sum_root + '.txt', format='ascii')

            tab.rename_column('col' + str(len(tab.colnames) - 1), 'logZ')
            tab.rename_column('col' + str(len(tab.colnames)), 'maxlogL')

            for ff in range(len(self.all_param_names)):
                mean = 0 * len(self.all_param_names) + 1 + ff
                stdev = 1 * len(self.all_param_names) + 1 + ff
                maxlike = 2 * len(self.all_param_names) + 1 + ff
                maxapost = 3 * len(self.all_param_names) + 1 + ff
                tab.rename_column('col{0:d}'.format(mean),
                                  'Mean_' + self.all_param_names[ff])
                tab.rename_column('col{0:d}'.format(stdev),
                                  'StDev_' + self.all_param_names[ff])
                tab.rename_column('col{0:d}'.format(maxlike),
                                  'MaxLike_' + self.all_param_names[ff])
                tab.rename_column('col{0:d}'.format(maxapost),
                                  'MAP_' + self.all_param_names[ff])

            tab.write(sum_root + '.fits', overwrite=True)
        else:
            # Load from fits file, which is much faster.
            tab = Table.read(sum_root + '.fits')

        return tab

    def load_mnest_modes(self, remake_fits=False):
        """Load up the separate modes results into an astropy table.
        """
        # Get all the different mode files

        tab_list = []

        modes = glob.glob(self.outputfiles_basename + 'mode*.dat')
        if len(modes) < 1:
            # In rare cases, we don't have the .dat files (modified, re-split).
            # Then check the *.fits files.
            modes = glob.glob(self.outputfiles_basename + 'mode*.fits')
            
            if len(modes) < 1:
                print('No modes files! Did you run multinest_utils.separate_mode_files yet?')
            else:
                remake_fits = False

        for num, mode in enumerate(modes, start=0):
            mode_root = self.outputfiles_basename + 'mode' + str(num)

            if remake_fits or not os.path.exists(mode_root + '.fits'):
                # Load from text file (and make fits file)
                tab = Table.read(mode_root + '.dat', format='ascii')

                # Convert to log(likelihood) since Multinest records -2*logLikelihood
                tab['col2'] /= -2.0

                # Rename the parameter columns.
                tab.rename_column('col1', 'weights')
                tab.rename_column('col2', 'logLike')

                for ff in range(len(self.all_param_names)):
                    cc = 3 + ff
                    tab.rename_column('col{0:d}'.format(cc), self.all_param_names[ff])

                tab.write(mode_root + '.fits', overwrite=True)
            else:
                tab = Table.read(mode_root + '.fits')

            tab_list.append(tab)

        return tab_list

    def load_mnest_results_for_dynesty(self, remake_fits=False):
        """
        Make a Dynesty-style results object that can
        be used in the nicer plotting codes.
        """
        # Fetch the summary stats for the global solution
        stats = self.load_mnest_summary(remake_fits=remake_fits)
        stats = stats[0]

        # Load up all of the parameters. 
        data_tab = self.load_mnest_results(remake_fits=remake_fits)
            
        # Sort the samples by increasing log-like.
        sdx = data_tab['logLike'].argsort()
        data_tab = data_tab[sdx]

        weights = data_tab['weights']
        loglike = data_tab['logLike']
        
        samples = np.zeros((len(data_tab), len(self.all_param_names)), dtype=float)
        for ff in range(len(self.all_param_names)):
            samples[:, ff] = data_tab[self.all_param_names[ff]].astype(np.float64)

        logZ = stats['logZ']
        logvol = np.log(weights) - loglike + logZ
        logvol = logvol - logvol.max()

        results = dict(samples=samples, weights=weights, logvol=logvol, loglike=loglike)

        return results


    def load_mnest_modes_results_for_dynesty(self, remake_fits=False):
        """
        Make a Dynesty-style results object that can 
        be used in the nicer plotting codes. 
        """
        results_list = []

        # Load up the summary results and trim out the global mode.
        stats = self.load_mnest_summary(remake_fits=remake_fits)
        stats = stats[1:]

        # Load up all of the parameters. 
        modes_list = self.load_mnest_modes(remake_fits=remake_fits)

        for num, data_tab in enumerate(modes_list, start=0):
            # Sort the samples by increasing log-like.
            sdx = data_tab['logLike'].argsort()
            data_tab = data_tab[sdx]

            weights = data_tab['weights']
            loglike = data_tab['logLike']
            
            samples = np.zeros((len(data_tab), len(self.all_param_names)), dtype=float)
            for ff in range(len(self.all_param_names)):
                samples[:, ff] = data_tab[self.all_param_names[ff]].astype(np.float64)

            logZ = stats['logZ'][num] # are these in the same order? 
            logvol = np.log(weights) - loglike + logZ
            logvol = logvol - logvol.max()
            
            results = dict(samples=samples, weights=weights, logvol=logvol, loglike=loglike)

            results_list.append(results)

        return results_list

    def plot_dynesty_style(self, sim_vals=None, fit_vals=None, remake_fits=False, dims=None,
                           traceplot=True, cornerplot=True, kde=True):
        """
        sim_vals : dict
            Dictionary of simulated input or comparison values to 
            overplot on posteriors.

        fit_vals : str
            Choices are 'map' (maximum a posteriori), 'mean', or
            'maxl' (maximum likelihood)

        """
        res = self.load_mnest_results_for_dynesty(remake_fits=remake_fits)
        smy = self.load_mnest_summary(remake_fits=remake_fits)

        truths = None

        # Sort the parameters into the right order.
        if sim_vals != None:
            truths = []
            for param in self.all_param_names:
                if param in sim_vals:
                    truths.append(sim_vals[param])
                else:
                    truths.append(None)

        if fit_vals == 'map':
            truths = []
            for param in self.all_param_names:
                truths.append(smy['MAP_' + param][0])  # global best fit.

        if fit_vals == 'mean':
            truths = []
            for param in self.all_param_names:
                truths.append(smy['Mean_' + param][0])  # global best fit.

        if fit_vals == 'maxl':
            truths = []
            for param in self.all_param_names:
                truths.append(smy['MaxLike_' + param][0])  # global best fit.

        if dims is not None:
            labels=[self.all_param_names[i] for i in dims]
            truths=[truths[i] for i in dims]
        else:
            labels=self.all_param_names

        if traceplot:
            dyplot.traceplot(res, labels=labels, dims=dims,
                             show_titles=True, truths=truths, kde=kde)
            plt.subplots_adjust(hspace=0.7)
            plt.savefig(self.outputfiles_basename + 'dy_trace.png')
            plt.close()

        if cornerplot:
            dyplot.cornerplot(res, labels=labels, dims=dims,
                              show_titles=True, truths=truths)
            ax = plt.gca()
            ax.tick_params(axis='both', which='major', labelsize=10)
            plt.savefig(self.outputfiles_basename + 'dy_corner.png')
            plt.close()

        return

    def plot_model_and_data(self, model,
                            input_model=None, mnest_results=None, suffix='',
                            zoomx=None, zoomy=None, zoomy_res=None, fitter=None,
                            N_traces=50):
        """
        Make and save the model and data plots.

        zoomx, xoomy, zoomy_res : list the same length as self.n_phot_sets
        Each entry of the list is a list [a, b] cooresponding to the plot limits
        """
        if model.photometryFlag:
            for i in range(self.n_phot_sets):
                if hasattr(model, 'use_gp_phot'):
                    if model.use_gp_phot[i]:
                        gp = True
                    else:
                        gp = False
                else:
                    gp = False

#                if gp:
#                    pointwise_likelihood(self.data, model, filt_index=i)
#                    debug_gp_nan(self.data, model, filt_index=i)

                fig = plot_photometry(self.data, model, input_model=input_model,
                                      dense_time=True, residuals=True,
                                      filt_index=i, mnest_results=mnest_results, gp=gp, fitter=fitter,
                                      N_traces=N_traces)
                fig.savefig(self.outputfiles_basename
                                + 'phot_and_residuals_'
                                + str(i + 1) + suffix + '.png')
                plt.close()
                
                if (zoomx is not None) or (zoomy is not None) or (zoomy_res is not None):
                    if zoomx is not None:
                        zoomxi=zoomx[i]
                    else:
                        zoomxi=None
    
                    if zoomy is not None:
                        zoomyi=zoomy[i]
                    else:
                        zoomyi=None
    
                    if zoomy_res is not None:
                        zoomy_resi=zoomy_res[i]
                    else:
                        zoomy_resi=None
    
                    fig = plot_photometry(self.data, model, input_model=input_model,
                                          dense_time=True, residuals=True,
                                          filt_index=i, mnest_results=mnest_results,
                                          zoomx=zoomxi, zoomy=zoomyi, zoomy_res=zoomy_resi, 
                                          gp=gp, fitter=fitter, N_traces=N_traces)
                    fig.savefig(self.outputfiles_basename
                                + 'phot_and_residuals_'
                                + str(i + 1) + suffix + 'zoom.png')
                    plt.close()
    
                if gp:
                    fig = plot_photometry_gp(self.data, model, input_model=input_model,
                                             dense_time=True, residuals=True,
                                             filt_index=i, mnest_results=mnest_results, gp=gp,
                                             N_traces=N_traces)
                    if fig is not None:
                        fig.savefig(self.outputfiles_basename
                                    + 'phot_and_residuals_gp_'
                                    + str(i + 1) + suffix + '.png')
                        plt.close()
    
                    if (zoomx is not None) or (zoomy is not None) or (zoomy_res is not None):
                        if zoomx is not None:
                            zoomxi=zoomx[i]
                        else:
                            zoomxi=None
                            
                        if zoomy is not None:
                            zoomyi=zoomy[i]
                        else:
                            zoomyi=None
    
                        if zoomy_res is not None:
                            zoomy_resi=zoomy_res[i]
                        else:
                            zoomy_resi=None
    
                        fig = plot_photometry_gp(self.data, model, input_model=input_model,
                                                 dense_time=True, residuals=True,
                                                 filt_index=i, mnest_results=mnest_results,
                                                 zoomx=zoomxi, zoomy=zoomyi, zoomy_res=zoomy_resi, gp=gp,
                                                 N_traces=N_traces)
                        if fig is not None:
                            fig.savefig(self.outputfiles_basename
                                        + 'phot_and_residuals_gp_'
                                        + str(i + 1) + suffix + 'zoom.png')
                            plt.close()
        
        if model.astrometryFlag:
            for i in range(self.n_ast_sets):
                # If no photometry
                if len(self.map_phot_idx_to_ast_idx) == 0:
                    fig_list = plot_astrometry(self.data, model,
                                               input_model=input_model,
                                               dense_time=True,
                                               n_phot_sets=self.n_phot_sets,
                                               filt_index=i,
                                               ast_filt_index=i,
                                               mnest_results=mnest_results, fitter=fitter,
                                               N_traces=N_traces)
                # If photometry
                else:
                    fig_list = plot_astrometry(self.data, model,
                                               input_model=input_model,
                                               dense_time=True,
                                               n_phot_sets=self.n_phot_sets,
                                               filt_index=i,
                                               ast_filt_index=self.map_phot_idx_to_ast_idx[i],
                                               mnest_results=mnest_results, fitter=fitter,
                                               N_traces=N_traces)

                fig_list[0].savefig(
                    self.outputfiles_basename + 'astr_on_sky_' + str(i + 1) + suffix + '.png')
                
                fig_list[1].savefig(
                    self.outputfiles_basename + 'astr_time_RA_' + str(i + 1) + suffix + '.png')

                fig_list[2].savefig(
                    self.outputfiles_basename + 'astr_time_Dec_' + str(i + 1) + suffix + '.png')

                fig_list[3].savefig(
                    self.outputfiles_basename + 'astr_time_RA_remove_pm_' + str(i + 1) + suffix + '.png')

                fig_list[4].savefig(
                    self.outputfiles_basename + 'astr_time_Dec_remove_pm_' + str(i + 1) + suffix + '.png')

                fig_list[5].savefig(
                    self.outputfiles_basename + 'astr_remove_pm_' + str(i + 1) + suffix + '.png')

                fig_list[6].savefig(
                    self.outputfiles_basename + 'astr_on_sky_unlensed' + suffix + '.png')

                fig_list[7].savefig(
                    self.outputfiles_basename + 'astr_longtime_RA_remove_pm' + suffix + '.png')

                fig_list[8].savefig(
                    self.outputfiles_basename + 'astr_longtime_Dec_remove_pm' + suffix + '.png')

                fig_list[9].savefig(
                    self.outputfiles_basename + 'astr_longtime_remove_pm' + suffix + '.png')

                for fig in fig_list:
                    plt.close(fig)

        return

    def plot_model_and_data_modes(self, def_best='maxl'):
        """
        Plots photometry data, along with n random draws from the posterior.
        """
        pspl_mod_list = self.get_best_fit_modes_model(def_best=def_best)
        for num, pspl_mod in enumerate(pspl_mod_list, start=0):
            model = pspl_mod

            self.plot_model_and_data(model, suffix='_mode' + str(num))

        return

    def summarize_results(self, def_best='maxl', remake_fits=False):
        tab = self.load_mnest_results(remake_fits=remake_fits)
        smy = self.load_mnest_summary(remake_fits=remake_fits)

        if len(tab) < 1:
            print('Did you run multinest_utils.separate_mode_files yet?')

            # Which params to include in table
        parameters = tab.colnames
        parameters.remove('weights')
        parameters.remove('logLike')

        print('####################')
        print('Median Solution:')
        print('####################')
        fmt_med = '    {0:15s}  {1:10.3f} + {2:10.3f} - {3:10.3f}'
        fmt_other = '    {0:15s}  {1:10.3f}'

        best_arr = self.get_best_fit(def_best='median')
        best = best_arr[0]
        errs = best_arr[1]
        for n in parameters:
            print(fmt_med.format(n, best[n], errs[n][0], errs[n][1]))
        self.print_likelihood(params=best)
        print('')

        print('####################')
        print('Max-likelihood Solution:')
        print('####################')
        best = self.get_best_fit(def_best='maxl')
        for n in parameters:
            print(fmt_other.format(n, best[n]))
        self.print_likelihood(params=best)
        print('')

        print('####################')
        print('MAP Solution:')
        print('####################')
        best = self.get_best_fit(def_best='map')
        for n in parameters:
            print(fmt_other.format(n, best[n]))
        self.print_likelihood(params=best)
        print('')

        return

    def summarize_results_modes(self, remake_fits=False):
        tab_list = self.load_mnest_modes(remake_fits=remake_fits)
        smy = self.load_mnest_summary(remake_fits=remake_fits)

        if len(tab_list) < 1:
            print('Did you run multinest_utils.separate_mode_files yet?')

        print('Number of modes : ' + str(len(tab_list)))

        for ii, tab in enumerate(tab_list, 1):
            # Which params to include in table
            parameters = tab.colnames
            parameters.remove('weights')
            parameters.remove('logLike')

            print('####################')
            print('Median Solution:')
            print('####################')
            fmt_med = '    {0:15s}  {1:10.3f} + {2:10.3f} - {3:10.3f}'
            fmt_other = '    {0:15s}  {1:10.3f}'

            best_arr = self.calc_best_fit(tab=tab, smy=smy, s_idx=ii,
                                          def_best='median')
            best = best_arr[0]
            errs = best_arr[1]
            for n in parameters:
                print(fmt_med.format(n, best[n], errs[n][0], errs[n][1]))
            self.print_likelihood(params=best)
            print('')

            print('####################')
            print('Max-likelihood Solution:')
            print('####################')
            best = self.calc_best_fit(tab=tab, smy=smy, s_idx=ii,
                                      def_best='maxl')
            for n in parameters:
                print(fmt_other.format(n, best[n]))
            self.print_likelihood(params=best)
            print('')

            print('####################')
            print('MAP Solution:')
            print('####################')
            best = self.calc_best_fit(tab=tab, smy=smy, s_idx=ii,
                                      def_best='map')
            for n in parameters:
                print(fmt_other.format(n, best[n]))
            self.print_likelihood(params=best)
            print('')

        return

    def print_likelihood(self, params='best', verbose=True):
        """
        Optional Inputs
        ------
        model_params : str or dict
            model_params = 'best' will load up the best solution and calculate
                the chi^2 based on those values. Alternatively, pass in a dictionary
                with the model parameters to use.
        """
        if params == 'best':
            params = self.get_best_fit()

        lnL = self.log_likely(params, verbose)
        chi2 = self.calc_chi2(params, verbose)

        print('logL :           {0:.1f}'.format(lnL))
        print('chi2 :           {0:.1f}'.format(chi2))

        return

    def calc_chi2(self, params='best', verbose=False):
        """
        Optional Inputs
        ------
        params : str or dict
            model_params = 'best' will load up the best solution and calculate
            the chi^2 based on those values. Alternatively, pass in a dictionary
            with the model parameters to use.
        """
        if params == 'best':
            params = self.get_best_fit()

        # Get likelihoods.
        pspl = self.get_model(params)
        lnL_phot = self.log_likely_photometry(pspl, params)
        lnL_ast = self.log_likely_astrometry(pspl)

        # Calculate constants needed to subtract from lnL to calculate chi2.
        if pspl.astrometryFlag:

            # Lists to store lnL, chi2, and constants for each filter.
            chi2_ast_filts = []
            lnL_const_ast_filts = []

            for nn in range(self.n_ast_sets):
                t_ast = self.data['t_ast' + str(nn + 1)]
                x = self.data['xpos' + str(nn + 1)]
                y = self.data['ypos' + str(nn + 1)]
                xerr = self.data['xpos_err' + str(nn + 1)]
                yerr = self.data['ypos_err' + str(nn + 1)]

                # Calculate the lnL for just a single filter.
                # If no photometry
                if len(self.map_phot_idx_to_ast_idx) == 0:
                    lnL_ast_nn = pspl.log_likely_astrometry(t_ast, x, y, xerr, yerr, ast_filt_idx=nn)
                # If photometry
                else:
                    lnL_ast_nn = pspl.log_likely_astrometry(t_ast, x, y, xerr, yerr, ast_filt_idx=self.map_phot_idx_to_ast_idx[nn])
                lnL_ast_nn = lnL_ast_nn.sum()

                # Calculate the chi2 and constants for just a single filter.
                lnL_const_ast_nn = -0.5 * np.log(2.0 * math.pi * xerr ** 2)
                lnL_const_ast_nn += -0.5 * np.log(2.0 * math.pi * yerr ** 2)
                lnL_const_ast_nn = lnL_const_ast_nn.sum()
                chi2_ast_nn = (lnL_ast_nn - lnL_const_ast_nn) / -0.5
                # Save to our lists
                chi2_ast_filts.append(chi2_ast_nn)
                lnL_const_ast_filts.append(lnL_const_ast_nn)

            lnL_const_ast = sum(lnL_const_ast_filts)

        else:
            lnL_const_ast = 0

        if pspl.photometryFlag:

            # Lists to store lnL, chi2, and constants for each filter.
            chi2_phot_filts = []
            lnL_const_phot_filts = []
        
            for nn in range(self.n_phot_sets):
                if hasattr(pspl, 'use_gp_phot'):
                    if pspl.use_gp_phot[nn]:
                        gp = True
                    else:
                        gp = False
                else:
                    gp = False
                t_phot = self.data['t_phot' + str(nn + 1)]
                mag = self.data['mag' + str(nn + 1)]
                mag_err = self.get_modified_mag_err(params, nn)
                
                # Calculate the lnL for just a single filter.
                lnL_phot_nn = pspl.log_likely_photometry(t_phot, mag, mag_err, nn)

                # Calculate the chi2 and constants for just a single filter.
                if gp:
                    log_det = pspl.get_log_det_covariance(t_phot, mag, mag_err, nn)
                    lnL_const_phot_nn = -0.5 * log_det - 0.5 * np.log(2 * np.pi) * len(mag)
                else:
                    lnL_const_phot_nn = -0.5 * np.log(2.0 * math.pi * mag_err**2)
                    lnL_const_phot_nn = lnL_const_phot_nn.sum()
                
                chi2_phot_nn = (lnL_phot_nn - lnL_const_phot_nn) / -0.5
    
                # Save to our lists
                chi2_phot_filts.append(chi2_phot_nn)
                lnL_const_phot_filts.append(lnL_const_phot_nn)
    
            lnL_const_phot = sum(lnL_const_phot_filts)
    
        else:
            lnL_const_phot = 0

        # Calculate chi2.
        chi2_ast = (lnL_ast - lnL_const_ast) / -0.5
        chi2_phot = (lnL_phot - lnL_const_phot) / -0.5
        chi2 = chi2_ast + chi2_phot

        if verbose:
            fmt = '{0:13s} = {1:f} '
            if pspl.photometryFlag:
                for ff in range(self.n_phot_sets):
                    print(fmt.format('chi2_phot' + str(ff + 1), chi2_phot_filts[ff]))

            if pspl.astrometryFlag:
                for ff in range(self.n_ast_sets):
                    print(fmt.format('chi2_ast' + str(ff + 1), chi2_ast_filts[ff]))
                
            print(fmt.format('chi2_phot', chi2_phot))
            print(fmt.format('chi2_ast', chi2_ast))
            print(fmt.format('chi2', chi2))

        return chi2


    def calc_chi2_manual(self, params='best', verbose=False):
        """
        Optional Inputs
        ------
        params : str or dict
            model_params = 'best' will load up the best solution and calculate
            the chi^2 based on those values. Alternatively, pass in a dictionary
            with the model parameters to use.
        """
        if params == 'best':
            params = self.get_best_fit()

        pspl = self.get_model(params)

        if pspl.astrometryFlag:

            # Lists to store lnL, chi2, and constants for each filter.
            chi2_ast_filts = []

            pspl = self.get_model(params)
        
            for nn in range(self.n_ast_sets):
                t_ast = self.data['t_ast' + str(nn + 1)]
                x = self.data['xpos' + str(nn + 1)]
                y = self.data['ypos' + str(nn + 1)]
                xerr = self.data['xpos_err' + str(nn + 1)]
                yerr = self.data['ypos_err' + str(nn + 1)]

                # NOTE: WILL BREAK FOR LUMINOUS LENS
                pos_out = pspl.get_astrometry(t_ast, ast_filt_idx=nn)

                chi2_ast_nn = (x - pos_out[:,0])**2/xerr**2
                chi2_ast_nn += (y - pos_out[:,1])**2/yerr**2

                chi2_ast_filts.append(np.nansum(chi2_ast_nn))
        else:
            chi2_ast_filts = [0]

        if pspl.photometryFlag:
            # Lists to store lnL, chi2, and constants for each filter.
            chi2_phot_filts = []

            for nn in range(self.n_phot_sets):
                if hasattr(pspl, 'use_gp_phot'):
                    if pspl.use_gp_phot[nn]:
                        gp = True
                    else:
                        gp = False
                else:
                    gp = False

                t_phot = self.data['t_phot' + str(nn + 1)]
                mag = self.data['mag' + str(nn + 1)]
                mag_err = self.get_modified_mag_err(params, nn)

                if gp:
                    print('GP')
                    mod_m_at_dat, mod_m_at_dat_std = pspl.get_photometry_with_gp(t_phot, mag, mag_err, nn)

                    print(pspl.get_log_det_covariance(t_phot, mag, mag_err, nn))
                    mag_out = mod_m_at_dat
                    mag_err_out = mod_m_at_dat_std
                    chi2_phot_nn = (mag - mag_out)**2/mag_err_out**2
                else:
                    mag_out = pspl.get_photometry(t_phot, nn)
                    chi2_phot_nn = (mag - mag_out)**2/mag_err**2
                
#                chi2_phot_nn = (mag - mag_out)**2/mag_err**2
                
                chi2_phot_filts.append(np.nansum(chi2_phot_nn))
                print('NANs : ' + str(np.sum(np.isnan(chi2_phot_nn))))

        else:
            chi2_phot_filts = [0]
        if verbose:
            fmt = '{0:13s} = {1:f} '
            if pspl.photometryFlag:
                for ff in range(self.n_phot_sets):
                    print(fmt.format('chi2_phot' + str(ff + 1), chi2_phot_filts[ff]))

            if pspl.astrometryFlag:
                for ff in range(self.n_ast_sets):
                    print(fmt.format('chi2_ast' + str(ff + 1), chi2_ast_filts[ff]))

            chi2 = np.sum(chi2_ast_filts) + np.sum(chi2_phot_filts)
                
#            print(fmt.format('chi2_phot', chi2_phot))
#            print(fmt.format('chi2_ast', chi2_ast))
#            print(fmt.format('chi2', chi2))
#
        return chi2

    def write_summary_maxL(self, return_mnest_results=False):
        tab = self.load_mnest_results()
        smy = self.load_mnest_summary()
        parameters = tab.colnames

        fmt   = '{0:15s}  {1:10.3f}'
        fmt_i = '{0:15s}  {1:10d}'

        k = self.n_dims
        n_phot = 0
        n_ast = 0
        for nn in range(self.n_phot_sets):
            n_phot += len(self.data['t_phot' + str(nn + 1)])
        if self.n_ast_sets > 0:
            for nn in range(self.n_ast_sets):
                n_ast += 2 * len(self.data['t_ast' + str(nn + 1)])
        n_tot = n_phot + n_ast

        maxlogL = smy['maxlogL'][0]
        aic = calc_AIC(k, maxlogL)
        bic = calc_BIC(n_tot, k, maxlogL)

        parameters.remove('weights')
        parameters.remove('logLike')

        best = self.get_best_fit(def_best='maxl')
        chi2 = self.calc_chi2(params=best, verbose=True)
        lnL = self.log_likely(cube
                                  =best, verbose=True)

        # Fetch the root name of the file.
        file_dir, name_str = os.path.split(self.outputfiles_basename)

        with open(name_str + 'maxL_summary.txt', 'w+') as myfile:
            myfile.write(file_dir + '\n')
            myfile.write(name_str + '\n')
            myfile.write(fmt.format('logL', maxlogL) + '\n')
            myfile.write(fmt.format('AIC', aic) + '\n')
            myfile.write(fmt.format('BIC', bic) + '\n')
            myfile.write(fmt.format('logL', lnL) + '\n')
            myfile.write(fmt.format('chi2', chi2) + '\n')
            myfile.write(fmt_i.format('n_tot', n_tot) + '\n')
            myfile.write('\n')
            for nn in parameters:
                myfile.write(fmt.format(nn, best[nn]) + '\n')

        if return_mnest_results:
            return tab
        else:
            return


class PSPL_Solver_weighted(PSPL_Solver):
    # Init should be inherited, right?
    # Does the prior dictionary get inherited?
    # Do we want to generalize to multiple astrometries?

    default_priors = {
        'mL': ('make_gen', 0, 100),
        't0': ('make_t0_gen', None, None),
        'xS0_E': ('make_xS0_gen', None, None),
        'xS0_N': ('make_xS0_gen', None, None),
        'u0_amp': ('make_gen', -1, 1),
        'beta': ('make_gen', -2, 2),
        'muL_E': ('make_gen', -20, 20),
        'muL_N': ('make_gen', -20, 20),
        'muS_E': ('make_muS_EN_gen', None, None),
        'muS_N': ('make_muS_EN_gen', None, None),
        'dL': ('make_gen', 1000, 8000),
        'dL_dS': ('make_gen', 0.01, 0.99),
        'b_sff': ('make_gen', 0.0, 1.5),
        'mag_src': ('make_gen', 17.0, 22.0),
        'tE': ('make_gen', 1, 400),
        'piE_E': ('make_gen', -1, 1),
        'piE_N': ('make_gen', -1, 1),
        'thetaE': ('make_gen', 0, 8),
        'q': ('make_gen', 0.001, 1),
        'alpha': ('make_gen', 0, 360),
        'phi': ('make_gen', 0, 360),
        'sep': ('make_gen', 1e-4, 2e-2),
        'piS': ('make_piS', None, None),
        'fdfdt': ('make_fdfdt', None, None),
        'log_thetaE': ('make_gen', -3, 1),
        'add_err': ('make_gen', 0, 0.3),
        'mult_err': ('make_gen', 1.0, 3.0)
    }

    def __init__(self, data, model_class,
                 custom_additional_param_names=None,
                 wrapped_params=None,
                 importance_nested_sampling=False,
                 multimodal=True, const_efficiency_mode=False,
                 n_live_points=300,
                 evidence_tolerance=0.5, sampling_efficiency=0.8,
                 n_iter_before_update=100, null_log_evidence=-1e90,
                 max_modes=100, mode_tolerance=-1e90,
                 outputfiles_basename="chains/1-", seed=-1, verbose=False,
                 resume=False, context=0, write_output=True, log_zero=-1e100,
                 max_iter=0, init_MPI=False, dump_callback=None,
                 weight=None):

        # Set the data, model, and error modes
        self.data = data
        self.model_class = model_class
        self.weight = weight

        # Check the data
        self.check_data()

        # Set up parameterization of the model
        self.photometry_params = ['b_sff', 'mag_src', 'add_err', 'mult_err']
        self.remove_digits = str.maketrans('', '',
                                           digits)  # removes nums from strings
        self.custom_additional_param_names = custom_additional_param_names
        self.n_phot_sets = None
        self.fitter_param_names = None
        self.additional_param_names = None
        self.all_param_names = None
        self.n_dims = None
        self.n_params = None
        self.n_clustering_params = None
        self.setup_params()

        # Set multinest stuff
        self.multimodal = multimodal
        self.wrapped_params = wrapped_params
        self.importance_nested_sampling = importance_nested_sampling
        self.const_efficiency_mode = const_efficiency_mode
        self.n_live_points = n_live_points
        self.evidence_tolerance = evidence_tolerance
        self.sampling_efficiency = sampling_efficiency
        self.n_iter_before_update = n_iter_before_update
        self.null_log_evidence = null_log_evidence
        self.max_modes = max_modes
        self.mode_tolerance = mode_tolerance
        self.outputfiles_basename = outputfiles_basename
        self.seed = seed
        self.verbose = verbose
        self.resume = resume
        self.context = context
        self.write_output = write_output
        self.log_zero = log_zero
        self.max_iter = max_iter
        self.init_MPI = init_MPI
        self.dump_callback = dump_callback

        # Setup the default priors
        self.priors = None
        self.make_default_priors()

        # Make the output directory if doesn't exist
        if os.path.dirname(outputfiles_basename) != '':
            os.makedirs(os.path.dirname(outputfiles_basename), exist_ok=True)

        return

    def calc_weight(self, weight):
        """
        order of weight_arr is 
        [phot_1, phot_2, ... phot_n, ast]
        """
        weight_arr = np.ones(len(self.data['phot_data']) + len(self.data['ast_data']))

        # Calculate the number of photometry and astrometry data points
        n_ast = 0
        if n_ast_sets > 0:
            for nn in range(self.n_ast_sets):
                n_ast += 2 * len(self.data['t_ast' + str(nn + 1)])
        n_phot = 0
        for i in range(self.n_phot_sets):
            n_phot += len(self.data['t_phot' + str(i + 1)])

        #####
        # No weights
        #####
        if weight is None:
            return weight_arr

        #####
        # All the photometry is weighted equally to the astrometry.
        # The relative weights between the photometric data sets don't change.
        #####
        if weight == 'phot_ast_equal':
            # Photometry weights
            for i in range(self.n_phot_sets):
                n_i = len(self.data['t_phot' + str(i + 1)])
                weight_arr[i] = n_ast/n_i
            # Astrometry weight
            weight_arr[-1] = n_phot/n_ast

            return weight_arr
        
        #####
        # Each data set is given equal weights, regardless of photometry
        # or astrometry.
        #####
        if weight == 'all_equal':
            # Photometry weights
            for i in range(self.n_phot_sets):
                n_i = len(self.data['t_phot' + str(i + 1)])
                weight_arr[i] = 1/n_i
            # Astrometry weight
            weight_arr[-1] = 1/n_ast

            return weight_arr

        #####
        # Custom weights.
        #####
        else:
            # Check weight array is right length, all positive numbers.
            if not isinstance(weight, np.ndarray):
                raise Exception('weight needs to be a numpy array.')
            if len(weight_arr) != weight:
                raise Exception('weight array needs to be the same length as the number of data sets.')
            if len(np.where(weight < 0)[0]) > 0:
                raise Exception('weights must be positive.')

            return weight

            
    def log_likely_astrometry(self, model, weight):
        if model.astrometryFlag:
            lnL_ast = 0.0

            for i in range(self.n_ast_sets):
                t_ast = self.data['t_ast' + str(i + 1)]
                xpos = self.data['xpos' + str(i + 1)]
                ypos = self.data['ypos' + str(i + 1)]
                xpos_err = self.data['xpos_err' + str(i + 1)]
                ypos_err = self.data['ypos_err' + str(i + 1)]

                lnL_ast_i = model.log_likely_astrometry(t_ast, xpos, ypos, xpos_err, ypos_err) * weight
                lnL_ast += lnL_ast_i
                print('i : ', i)
                print('lnL_ast :', lnL_ast)

        else:
            lnL_ast = 0

        return lnL_ast


    def log_likely_photometry(self, model, cube, weights):
        if model.photometryFlag:
            lnL_phot = 0.0

            for i in range(self.n_phot_sets):
                t_phot = self.data['t_phot' + str(i + 1)]
                mag = self.data['mag' + str(i + 1)]
                
                # additive or multiplicative error
                mag_err = self.get_modified_mag_err(cube, i)  
                
                lnL_phot_i = model.log_likely_photometry(t_phot, mag, mag_err, i) * weights[i]
                lnL_phot += lnL_phot_i

        else:
            lnL_phot = 0

        return lnL_phot

    def log_likely(self, cube, verbose=False):
        """
        cube : list or dict
            The dictionary or cube of the model parameters.
        """

        weights_arr = self.calc_weight(self.weight)

        model = self.get_model(cube)

        lnL_phot = self.log_likely_photometry(model, cube, weights_arr[:-1])
        lnL_ast = self.log_likely_astrometry(model, weights_arr[-1])

        lnL = lnL_phot + lnL_ast

            
        if verbose:
            # self.plot_model_and_data(model)
            
            fmt = '{0:13s} = {1:f} '
            for ff in range(self.n_params):
                if isinstance(cube, dict) or isinstance(cube, Row):
                    pname = self.all_param_names[ff]
                    if ((isinstance(cube, dict) and pname in cube) or
                        (isinstance(cube, Row)  and pname in cube.colnames)):
                        print(fmt.format(pname, cube[pname])),
                    else:
                        print(fmt.format(pname, -999.0)),
                else:
                    print(fmt.format(self.all_param_names[ff], cube[ff])),
            print(fmt.format('lnL_phot', lnL_phot)),
            print(fmt.format('lnL_ast', lnL_ast)),
            print(fmt.format('lnL', lnL))

        return lnL

# Does calc_chi2 need to be fixed? 


#########################
### PRIOR GENERATORS  ###
#########################

def make_gen(min, max):
    return scipy.stats.uniform(loc=min, scale=max - min)


def make_norm_gen(mean, std):
    return scipy.stats.norm(loc=mean, scale=std)


def make_lognorm_gen(mean, std):
    """ Make a natural-log normal distribution for a variable.
    The specified mean and std should be in the ln() space.
    """
    return scipy.stats.lognorm(s=std, scale=np.exp(mean))

def make_log10norm_gen(mean_in_log10, std_in_log10):
    """Scale scipy lognorm from natural log to base 10.
    Note the mean and std should be in the log10() space already.

    mean : mean of the underlying log10 gaussian (i.e. a log10 quantity)
    std  : variance of underlying log10 gaussian
    """
    # Convert mean and std from log10 to ln.
    return scipy.stats.lognorm(s=std_in_log10 * np.log(10), scale=np.exp(mean_in_log10 * np.log(10)))

def make_truncnorm_gen(mean, std, lo_cut, hi_cut):
    """
    lo_cut and hi_cut are in the units of sigma
    """
    return scipy.stats.truncnorm(lo_cut, hi_cut,
                                 loc=mean, scale=std)


def make_truncnorm_gen_with_bounds(mean, std, low_bound, hi_bound):
    """
    low_bound and hi_bound are in the same units as mean and std
    """
    assert hi_bound > low_bound
    clipped_mean = min(max(mean, low_bound), hi_bound)

    if clipped_mean == low_bound:
        low_sigma = -0.01 * std
        hi_sigma = (hi_bound - clipped_mean) / std
    elif clipped_mean == hi_bound:
        low_sigma = (low_bound - clipped_mean) / std
        hi_sigma = 0.01 * std
    else:
        low_sigma = (low_bound - clipped_mean) / std
        hi_sigma = (hi_bound - clipped_mean) / std
    return scipy.stats.truncnorm(low_sigma, hi_sigma,
                                 loc=clipped_mean, scale=std)


def make_t0_gen(t, mag):
    """Get an approximate t0 search range by finding the brightest point
    and then searching days where flux is higher than 80% of this peak.
    """
    mag_min = np.min(mag)  # min mag = brightest
    delta_mag = np.max(mag) - mag_min
    idx = np.where(mag < (mag_min + (0.2 * delta_mag)))[0]
    t0_min = t[idx].min()
    t0_max = t[idx].max()

    # Pad by and extra 40% in case of gaps.
    t0_min -= 0.4 * (t0_max - t0_min)
    t0_max += 0.4 * (t0_max - t0_min)

    return make_gen(t0_min, t0_max)

def make_mag_base_gen(mag):
    """
    Make a prior for baseline magnitude using the data.
    """
    mean, med, std = sigma_clipped_stats(mag, sigma_lower=2, sigma_upper=4)

    gen = make_truncnorm_gen(mean, 3 * std, -5, 5)

    return gen

def make_mag_src_gen(mag):
    """
    Make a prior for source magnitude using the data.
    Allow negative blending.
    """
    mean, med, std = sigma_clipped_stats(mag, sigma_lower=2, sigma_upper=4)

    gen = make_gen(mean - 1, mean + 5) 

    return gen


def make_xS0_gen(pos):
    posmin = pos.min() - 5 * pos.std()
    posmax = pos.max() + 5 * pos.std()
#    print('make_xS0_gen')
#    print('posmin : ', posmin)
#    print('posmax : ', posmax)
#    print('         ')
    return make_gen(posmin, posmax)

def make_xS0_norm_gen(pos):
    posmid = 0.5 * (pos.min() + pos.max())
    poswidth = np.abs(pos.max() - pos.min())
#    print('make_xS0_norm_gen')
#    print('posmid : ', posmid)
#    print('poswidth : ', poswidth)
#    print('         ')
    return make_norm_gen(posmid, poswidth)


def make_muS_EN_gen(t, pos, scale_factor=100.0):
    """Get an approximate muS search range by looking at the best fit
    straight line to the astrometry. Then allows lots of free space.

    Inputs
    ------
    t: array of times in days
    pos: array of positions in arcsec

    Return
    ------
    uniform generator for velocity in mas/yr
    """
    # Convert t to years temporarily.
    t_yr = t / mmodel.days_per_year

    # Reshaping stuff... convert (1,N) array into (N,) array
    if (t_yr.ndim == 2 and t_yr.shape[0] == 1):
        t_yr = t_yr.reshape(len(t_yr[0]))
        pos = pos.reshape(len(pos[0]))

    par, cov = np.polyfit(t_yr, pos, 1, cov=True)
    vel = par[0] * 1e3  # mas/yr
    vel_err = (cov[0][0] ** 0.5) * 1e3  # mas/yr

    vel_lo = vel - scale_factor * vel_err
    vel_hi = vel + scale_factor * vel_err
#    print('make_muS_EN_gen')
#    print('vel_lo : ', vel_lo)
#    print('vel_hi : ', vel_hi)
#    print('         ')
    return make_gen(vel_lo, vel_hi)

def make_muS_EN_norm_gen(t, pos):
    """Get an approximate muS search range by looking at the best fit
    straight line to the astrometry. Then allows lots of free space.

    Inputs
    ------
    t: array of times in days
    pos: array of positions in arcsec

    Return
    ------
    uniform generator for velocity in mas/yr
    """
    # Convert t to years temporarily.
    t_yr = t / mmodel.days_per_year
    par, cov = np.polyfit(t_yr, pos, 1, cov=True)
    vel = par[0] * 1e3  # mas/yr
    vel_err = (cov[0][0] ** 0.5) * 1e3  # mas/yr

    scale_factor = 10.0

#    print('make_muS_EN_norm_gen')
#    print('vel : ', vel)
#    print('vel_1sigma : ', scale_factor * vel_err)
#    print('         ')
    return make_norm_gen(vel, scale_factor * vel_err)


def make_invgamma_gen(t_arr):
    """
    ADD DESCRIPTION
    t_arr = time array
    """
    a,b = compute_invgamma_params(t_arr)

#    print('inv gamma')
#    print('a : ', a)
#    print('b : ', b)

    return scipy.stats.invgamma(a, scale=b)


def compute_invgamma_params(t_arr):
    """
    Based on function of same name from 
    Fran Bartolic's ``caustic`` package:
    https://github.com/fbartolic/caustic
    Returns parameters of an inverse gamma distribution s.t.
    1% of total prob. mass is assigned to values of t < t_{min} and
    1% of total prob. masss  to values greater than t_{tmax}. 
    t_{min} is defined to be the median spacing between consecutive
    data points in the time series and t_{max} is the total duration
    of the time series.
    
    Parameters
    ----------
    t_arr : array
        Array of times
    Returns
    -------
    invgamma_a, invgamma_b : float (?)
        The parameters a,b of the inverse gamma function.
    """
    def solve_for_params(params, x_min, x_max):
        lower_mass = 0.01
        upper_mass = 0.99

        # Trial parameters
        alpha, beta = params

        # Equation for the roots defining params which satisfy the constraint
        cdf_l = scipy.stats.invgamma.cdf(x_min, alpha, scale=beta) - lower_mass,
        cdf_u = scipy.stats.invgamma.cdf(x_max, alpha, scale=beta) - upper_mass,

        return np.array([cdf_l, cdf_u]).reshape((2,))

    # Compute parameters for the prior on GP hyperparameters
    med_sep = np.median(np.diff(t_arr))
    tot_dur = t_arr[-1] - t_arr[0]
    invgamma_a, invgamma_b = scipy.optimize.fsolve(solve_for_params,
                                                   (0.001, 0.001),
                                                   (med_sep, tot_dur))

    return invgamma_a, invgamma_b

def make_piS():
    # piS prior comes from PopSyCLE:
    # We will assume a truncated normal distribution with only a small-side truncation at ~20 kpc.
    piS_mean = 0.1126  # mas
    piS_std = 0.0213  # mas
    piS_lo_cut = (0.05 - piS_mean) / piS_std  # sigma
    piS_hi_cut = 90.  # sigma
    return scipy.stats.truncnorm(piS_lo_cut, piS_hi_cut,
                                 loc=piS_mean, scale=piS_std)


def make_fdfdt():
    return scipy.stats.norm(loc=0, scale=1 / 365.25)


def random_prob(generator, x):
    value = generator.ppf(x)
    ln_prob = generator.logpdf(value)
    return value, ln_prob


def weighted_quantile(values, quantiles, sample_weight=None,
                      values_sorted=False, old_style=False):
    """ Very close to numplt.percentile, but supports weights.
    NOTE: quantiles should be in [0, 1]!
    :param values: numplt.array with data
    :param quantiles: array-like with many quantiles needed
    :param sample_weight: array-like of the same length as `array`
    :param values_sorted: bool, if True, then will avoid sorting of initial array
    :param old_style: if True, will correct output to be consistent with numplt.percentile.
    :return: numplt.array with computed quantiles.
    """
    values = np.array(values)
    quantiles = np.array(quantiles)
    if sample_weight is None:
        sample_weight = np.ones(len(values))
    sample_weight = np.array(sample_weight)
    assert np.all(quantiles >= 0) and np.all(
        quantiles <= 1), 'quantiles should be in [0, 1]'

    if not values_sorted:
        sorter = np.argsort(values)
        values = values[sorter]
        sample_weight = sample_weight[sorter]

    weighted_quantiles = np.cumsum(sample_weight) - 0.5 * sample_weight
    if old_style:
        # To be convenient with np.percentile
        weighted_quantiles -= weighted_quantiles[0]
        weighted_quantiles /= weighted_quantiles[-1]
    else:
        weighted_quantiles /= np.sum(sample_weight)

    return np.interp(quantiles, weighted_quantiles, values)

def split_param_filter_index1(s):
    """
    Split a parameter name into the <string><number> components
    where <string> is the parameter name and <number> is the filter
    index (1-based). If there is no number at the end for a filter
    index, then return None for the second argument.

    Returns
    ----------
    param_name : str
        The name of the parameter.
    filt_index : int (or None)
        The 1-based filter index.
    
    """
    param_name = s.rstrip('123456789')
    if len(param_name) == len(s):
        filt_index = None
    else:
        filt_index = int(s[len(param_name):])

    return param_name, filt_index

def generate_params_dict(params, fitter_param_names):
    """
    Take a list, dictionary, or astropy Row of fit parameters
    and extra parameters and convert it into a well-formed dictionary 
    that can be fed straight into a model object.

    The output object will only contain parameters specified
    by name in fitter_param_names. Multi-filter photometry
    parameters are treated specially and grouped together into an
    array such as ['mag_src'] = [mag_src1, mag_src2].

    Input
    ----------
    params : list, dict, Row
        Contains values of parameters. Note that if the
        params are in a list, they need to be in the same
        order as fitter_param_names. If the params are in 
        a dict or Row, then order is irrelevant.

    fitter_param_names : list
        The names of the parameters that will be
        delivered, in order, in the output. 

    Ouptut
    ----------
    params_dict : dict
        Dictionary of the parameter names and values.

    """
    skip_list = ['weights', 'logLike', 'add_err', 'mult_err']
    multi_list = ['mag_src', 'mag_base', 'b_sff']
    multi_dict = ['gp_log_rho', 'gp_log_S0', 'gp_log_sigma', 'gp_rho', 'gp_log_omega04_S0', 'gp_log_omega0']
    
    params_dict = {}
    
    for i, param_name in enumerate(fitter_param_names):
        # Skip some parameters.
        if any([x in param_name for x in skip_list]):
            continue

        if isinstance(params, (dict, Row)):
            key = param_name
        else:
            key = i

        # Check to see if this is a multi-filter parameter. None if not.
        filt_param, filt_idx = split_param_filter_index1(param_name)

        # Handle global parameters (not filter dependent)
        if filt_idx == None:
            params_dict[param_name] = params[key]
        else:
            # Handle filter dependent parameters... 2 cases (list=required vs. dict=optional)
            
            if filt_param in multi_list:
                # Handle the filter-dependent fit parameters (required params).  
                # They need to be grouped as a list for input into a model.
                if filt_param not in params_dict:
                    params_dict[filt_param] = []
                    
                # Add this filter to our list.
                params_dict[filt_param].append(params[key])

            if filt_param in multi_dict:
                # Handle the optional filter-dependent fit parameters (required params).  
                # They need to be grouped as a dicionary for input into a model.
                if filt_param not in params_dict:
                    params_dict[filt_param] = {}

                # Add this filter to our dict. Note the switch to 0-based here.
                params_dict[filt_param][filt_idx-1] = params[key]

    return params_dict
                    

########################################
### GENERAL USE AND SHARED FUNCTIONS ###
########################################

def pointwise_likelihood(data, model, filt_index=0):
    """
    Makes some plots to diagnose weirdness in GP fits.
    """
    # Get the data out.
    dat_t = data['t_phot' + str(filt_index + 1)]
    dat_m = data['mag' + str(filt_index + 1)]
    dat_me = data['mag_err' + str(filt_index + 1)]

    # Make models.
    # Decide if we sample the models at a denser time, or just the
    # same times as the measurements.
    pw_logL = np.zeros(len(dat_t))

    for tt, time in enumerate(dat_t):
        pw_logL[tt] = model.log_likely_photometry([dat_t[tt]], [dat_m[tt]], [dat_me[tt]], filt_index)

    return pw_logL

def debug_gp_nan(data, model, filt_index=0):
    """
    Makes some plots to diagnose weirdness in GP fits.
    """
    # Get the data out.
    dat_t = data['t_phot' + str(filt_index + 1)]
    dat_m = data['mag' + str(filt_index + 1)]
    dat_me = data['mag_err' + str(filt_index + 1)]

    # Make models.
    # Decide if we sample the models at a denser time, or just the
    # same times as the measurements.
    mod_m_out, mod_m_out_std = model.get_photometry_with_gp(dat_t, dat_m, dat_me, filt_index, dat_t)
    if mod_m_out is None:
        print('GP not working at prediction times!')
        mod_m_out = model.get_photometry(dat_t, filt_index)

    mod_m_at_dat, mod_m_at_dat_std = model.get_photometry_with_gp(dat_t, dat_m, dat_me, filt_index)
    bad_idx = np.nonzero(np.isnan(mod_m_at_dat))[0]
    print('Number of nan: ', str(len(bad_idx)))
    plt.figure(100, figsize=(10,10))
    plt.clf()
    plt.errorbar(dat_t, dat_m, yerr=dat_me, fmt='k.', alpha=0.2)
    plt.errorbar(dat_t[bad_idx], dat_m[bad_idx], yerr=dat_me[bad_idx], fmt='ro', alpha=1)
    plt.gca().invert_yaxis()
    plt.xlabel('Time')
    plt.ylabel('Mag')
    plt.savefig('nans.png')
    
    # Magnitude errors
    plt.figure(101, figsize=(6,6))
    plt.clf()
    plt.hist(dat_me, label='All', bins=np.linspace(0, np.max(dat_me), 50), alpha=0.5)
    plt.hist(dat_me[bad_idx], label='Bad', bins=np.linspace(0, np.max(dat_me), 50), alpha=0.5)
    plt.yscale('log')
    plt.xlabel('mag err')
    plt.legend()
    plt.savefig('nans_me_hist.png')
    
    # Difference between time of point N and point N-1.
    plt.figure(102, figsize=(6,6))
    plt.clf()
    plt.hist(dat_t[bad_idx] - dat_t[bad_idx-1], bins=np.logspace(-2, 2, 50), label='Bad', alpha=0.5)
    plt.hist(dat_t[1:] - dat_t[:-1], bins=np.logspace(-2, 2, 50), label='All', alpha=0.5)
    plt.xscale('log')
    plt.yscale('log')
    plt.xlabel('delta t (days)')
    plt.legend()
    plt.savefig('nans_deltat_hist.png')

def plot_photometry(data, model, input_model=None, dense_time=True, residuals=True,
                    filt_index=0, zoomx=None, zoomy=None, zoomy_res=None, mnest_results=None,
                    N_traces=50, gp=False, fitter=None):
    # Get the data out.
    dat_t = data['t_phot' + str(filt_index + 1)]
    dat_m = data['mag' + str(filt_index + 1)]
    dat_me = data['mag_err' + str(filt_index + 1)]

    # Make models.
    # Decide if we sample the models at a denser time, or just the
    # same times as the measurements.
    if dense_time:
        # 1 day sampling over whole range
        mod_t = np.arange(dat_t.min(), dat_t.max(), 0.1)
    else:
        mod_t = dat_t
    if gp:
        mod_m_out, mod_m_out_std = model.get_photometry_with_gp(dat_t, dat_m, dat_me, filt_index, mod_t)
        if mod_m_out is None:
            print('GP not working at prediction times!')
            mod_m_out = model.get_photometry(mod_t, filt_index)
        mod_m_at_dat, mod_m_at_dat_std = model.get_photometry_with_gp(dat_t, dat_m, dat_me, filt_index)
        if mod_m_at_dat is None:
            print('GP not working at data times!')
            mod_m_at_dat = model.get_photometry(dat_t, filt_index)
    else:
        mod_m_out = model.get_photometry(mod_t, filt_index)
        mod_m_at_dat = model.get_photometry(dat_t, filt_index)
        
    # Input Model
    if input_model != None:
        mod_m_in = input_model.get_photometry(mod_t, filt_index)

#    fig = plt.figure(1, figsize=(15,15))
    fig = plt.figure(1, figsize=(10,10))
    plt.clf()
#    plt.subplots_adjust(bottom=0.2, left=0.2)
    plt.subplots_adjust(bottom=0.2, left=0.3)

    # Decide if we are plotting residuals
    if residuals:
#        f1 = plt.gcf().add_axes([0.1, 0.3, 0.8, 0.6])
#        f1 = plt.gcf().add_axes([0.1, 0.35, 0.8, 0.55])
#        f2 = plt.gcf().add_axes([0.1, 0.1, 0.8, 0.2])
        f1 = plt.gcf().add_axes([0.2, 0.45, 0.7, 0.45])
        f2 = plt.gcf().add_axes([0.2, 0.15, 0.7, 0.25])
    else:
        plt.gca()

    #####
    # Data
    #####
    f1.errorbar(dat_t, dat_m, yerr=dat_me, fmt='k.', alpha=0.2, label='Data')
    if input_model != None:
        f1.plot(mod_t, mod_m_in, 'g-', label='Input')
    f1.plot(mod_t, mod_m_out, 'r-', label='Model')
    if gp and mod_m_out_std is not None:
        f1.fill_between(mod_t, mod_m_out+mod_m_out_std, mod_m_out-mod_m_out_std, 
                        color='r', alpha=0.3, edgecolor="none")
    f1.set_ylabel('I (mag)')
    f1.invert_yaxis()
    f1.set_title('Input Data and Output Model')
    f1.get_xaxis().set_visible(False)
    f1.set_xlabel('t - t0 (days)')
    f1.legend()
    if zoomx is not None:
        f1.set_xlim(zoomx[0], zoomx[1])

    if zoomy is not None:
        f1.set_ylim(zoomy[0], zoomy[1])
    #####
    # Traces
    #####
    if mnest_results is not None:
        idx_arr = np.random.choice(np.arange(len(mnest_results['weights'])),
                                   p=mnest_results['weights'],
                                   size=N_traces)
        trace_times = []
        trace_magnitudes = []
        for idx in idx_arr:
#            # FIXME: This doesn't work if there are additional_param_names in the model
#            # You will have extra arguments when passing in **params_dict into the model class.
#            # FIXME 2: there needs to be a way to deal with multiples in additional_param_names
#            params_dict = generate_params_dict(mnest_results[idx],
#                                               mnest_results.colnames)
#            
#            trace_mod = model.__class__(**params_dict,
#                                        raL=model.raL,
#                                        decL=model.decL)

            trace_mod = fitter.get_model(mnest_results[idx])

            if gp:
                trace_mag, trace_mag_std = trace_mod.get_photometry_with_gp(dat_t, dat_m, dat_me, filt_index, mod_t)
                if trace_mag_std is None:
                    print('GP is not working at model times!')
                continue
            else:
                trace_mag = trace_mod.get_photometry(mod_t, filt_index)

            trace_times.append(mod_t)
            trace_magnitudes.append(trace_mag)
            f1.plot(mod_t, trace_mag,
                    color='c',
                    alpha=0.5,
                    linewidth=1,
                    zorder=-1)
    #####
    # Residuals
    #####
    if residuals:
        f1.get_shared_x_axes().join(f1, f2)
        f2.errorbar(dat_t, dat_m - mod_m_at_dat,
                    yerr=dat_me, fmt='k.', alpha=0.2)
        f2.axhline(0, linestyle='--', color='r')
        f2.set_xlabel('Time (HJD)')
        f2.set_ylabel('Obs - Mod')
        if zoomx is not None:
            f2.set_xlim(zoomx[0], zoomx[1])
        if zoomy is not None:
            f2.set_ylim(zoomy[0], zoomy[1])
        if zoomy_res is not None:
            f2.set_ylim(zoomy_res[0], zoomy_res[1])

    return fig


def plot_photometry_gp(data, model, input_model=None, dense_time=True, residuals=True,
                    filt_index=0, zoomx=None, zoomy=None, zoomy_res=None, mnest_results=None,
                    N_traces=50, gp=False):

    gs_kw = dict(height_ratios=[1,2,1])
    fig, (ax1, ax2, ax3) = plt.subplots(nrows=3, ncols=1, sharex=True, 
                                        figsize=(15,15),
                                        gridspec_kw=gs_kw)
#    plt.clf()
    plt.subplots_adjust(bottom=0.1, left=0.1)

    # Get the data out.
    dat_t = data['t_phot' + str(filt_index + 1)]
    dat_m = data['mag' + str(filt_index + 1)]
    dat_me = data['mag_err' + str(filt_index + 1)]

    # Make models.
    # Decide if we sample the models at a denser time, or just the
    # same times as the measurements.
    if dense_time:
        # 1 day sampling over whole range
        mod_t = np.arange(dat_t.min(), dat_t.max(), 1)
    else:
        mod_t = dat_t

    mod_m_out_gp, mod_m_out_std_gp = model.get_photometry_with_gp(dat_t, dat_m, dat_me, filt_index, mod_t)
    mod_m_at_dat_gp, mod_m_at_dat_std_gp = model.get_photometry_with_gp(dat_t, dat_m, dat_me, filt_index)
    mod_m_out = model.get_photometry(mod_t, filt_index)
    mod_m_at_dat = model.get_photometry(dat_t, filt_index)
    if mod_m_out_gp is not None:
        # Input Model
        if input_model != None:
            mod_m_in = input_model.get_photometry(mod_t, filt_index)
    
        #####
        # Data only
        #####
        ax1.errorbar(dat_t, dat_m, yerr=dat_me, fmt='k.', alpha=0.2, label='Raw Data')
        ax1.set_ylabel('I (mag)')
        ax1.invert_yaxis()
        ax1.get_xaxis().set_visible(False)
        ax1.legend()
        
        #####
        # Data minus model (just GP)
        #####
        ax2.errorbar(dat_t, dat_m - (mod_m_at_dat_gp - mod_m_at_dat), yerr=dat_me, fmt='k.', alpha=0.2, label='Detrended data')
        ax2.plot(mod_t, mod_m_out, 'r-', label='Model', lw=1)
        ax2.set_ylabel('I (mag)')
        ax2.invert_yaxis()
        ax2.get_xaxis().set_visible(False)
        ax2.legend()
    
        #####
        # Data minus GP (just model/detrended data)
        #####
        ax3.axhline(y=0, color='dimgray', ls=':', alpha=0.8)
        ax3.errorbar(dat_t, dat_m - mod_m_at_dat, yerr=dat_me, fmt='k.', alpha=0.2, label='Correlated Noise')
        ax3.plot(mod_t, mod_m_out_gp - mod_m_out, 'r-', label='GP', lw=1, zorder=5000)
        ax3.set_ylabel('I (mag)')
        ax3.invert_yaxis()
        ax3.set_xlabel('Time (HJD)')
        ax3.legend()
    
        if zoomx is not None:
            ax1.set_xlim(zoomx[0], zoomx[1])
            ax2.set_xlim(zoomx[0], zoomx[1])
            ax3.set_xlim(zoomx[0], zoomx[1])
        if zoomy is not None:
            ax1.set_ylim(zoomy[0], zoomy[1])
            ax2.set_ylim(zoomy[0], zoomy[1])
        if zoomy_res is not None:
            ax3.set_ylim(zoomy_res[0], zoomy_res[1])
    
        return fig
    else:    
        return None

def plot_astrometry(data, model, input_model=None, dense_time=True,
                    residuals=True, n_phot_sets=0, filt_index=0, ast_filt_index=0,
                    mnest_results=None, N_traces=50, fitter=None):
    #####
    # Astrometry on the sky
    #####
    fig_list = []
    plt.close(n_phot_sets + 1)
    fig = plt.figure(n_phot_sets + 1, figsize=(10, 10))  # PLOT 1
    fig_list.append(fig)
    plt.clf()
    
    # Get the data out.
    dat_x = data['xpos' + str(filt_index + 1)] * 1e3
    dat_y = data['ypos' + str(filt_index + 1)] * 1e3
    dat_xe = data['xpos_err' + str(filt_index + 1)] * 1e3
    dat_ye = data['ypos_err' + str(filt_index + 1)] * 1e3
    dat_t = data['t_ast' + str(filt_index + 1)]

    if (dat_xe.ndim == 2 and dat_xe.shape[0] == 1):
        dat_t = dat_t.reshape(len(dat_t[0]))
        dat_x = dat_x.reshape(len(dat_x[0]))
        dat_y = dat_y.reshape(len(dat_y[0]))
        dat_xe = dat_xe.reshape(len(dat_xe[0]))
        dat_ye = dat_ye.reshape(len(dat_ye[0]))

    # Data
    plt.errorbar(dat_x, dat_y, xerr=dat_xe, yerr=dat_ye,
                 fmt='k.', label='Data')

    # Decide if we sample the models at a denser time, or just the
    # same times as the measurements.
    if dense_time:
        # 1 day sampling over whole range
        t_mod = np.arange(dat_t.min(), dat_t.max(), 1)
    else:
        t_mod = dat_t

    # Model - usually from fitter
    pos_out = model.get_astrometry(t_mod, ast_filt_idx=ast_filt_index)
    plt.plot(pos_out[:, 0] * 1e3, pos_out[:, 1] * 1e3, 'r-', label='Model')

    # Input model
    if input_model != None:
        pos_in = input_model.get_astrometry(t_mod, ast_filt_idx=ast_filt_index)
        plt.plot(pos_in[:, 0] * 1e3, pos_in[:, 1] * 1e3, 'g-', label='Input Model')

    #####
    # Traces
    #####
    if mnest_results is not None:
        idx_arr = np.random.choice(np.arange(len(mnest_results['weights'])),
                                   p=mnest_results['weights'],
                                   size=N_traces)
        trace_posxs = []
        trace_posys = []
        trace_posxs_no_pm = []
        trace_posys_no_pm = []

        for idx in idx_arr:
            trace_mod = fitter.get_model(mnest_results[idx])

            trace_pos = trace_mod.get_astrometry(t_mod, ast_filt_idx=ast_filt_index)
            trace_pos_no_pm = trace_mod.get_astrometry(t_mod, ast_filt_idx=ast_filt_index) - trace_mod.get_astrometry_unlensed(t_mod)

            trace_posxs.append(trace_pos[:, 0] * 1e3)
            trace_posys.append(trace_pos[:, 1] * 1e3)
            trace_posxs_no_pm.append(trace_pos_no_pm[:, 0] * 1e3)
            trace_posys_no_pm.append(trace_pos_no_pm[:, 1] * 1e3)

    if mnest_results is not None:
        for idx in np.arange(len(idx_arr)):
            plt.plot(trace_posxs[idx], trace_posys[idx], 
                     color='c',
                     alpha=0.5,
                     linewidth=1,
                     zorder=-1)

    plt.gca().invert_xaxis()
    plt.xlabel(r'$\Delta \alpha^*$ (mas)')
    plt.ylabel(r'$\Delta \delta$ (mas)')
    plt.legend(fontsize=12)


    #####
    # Astrometry vs. time
    # x = RA, y = Dec
    #####

    plt.close(n_phot_sets + 2)
    fig = plt.figure(n_phot_sets + 2, figsize=(10, 10))  # PLOT 2
    fig_list.append(fig)
    plt.clf()
    plt.subplots_adjust(bottom=0.25, left=0.25)

    # Decide if we're plotting residuals
    if residuals:
        f1 = plt.gcf().add_axes([0.15, 0.3, 0.8, 0.6])
        f2 = plt.gcf().add_axes([0.15, 0.1, 0.8, 0.2])
    else:
        plt.gca()

    f1.errorbar(dat_t, dat_x, yerr=dat_xe, fmt='k.', label='Data')
    f1.plot(t_mod, pos_out[:, 0] * 1e3, 'r-', label='Model')
    if input_model != None:
        f1.plot(t_mod, pos_in[:, 0] * 1e3, 'g-', label='Input Model')
    f1.set_xlabel('t - t0 (days)')
    f1.set_ylabel(r'$\Delta \alpha^*$ (mas)')
    f1.legend()

    # Decide if plotting traces
    if mnest_results is not None:
        for idx in np.arange(len(idx_arr)):
            f1.plot(t_mod, trace_posxs[idx], 
                    color='c',
                    alpha=0.5,
                    linewidth=1,
                    zorder=-1)

    if residuals:
        f1.get_xaxis().set_visible(False)
        f1.get_shared_x_axes().join(f1, f2)
        f2.errorbar(dat_t, dat_x - model.get_astrometry(dat_t, ast_filt_idx=ast_filt_index)[:,0] * 1e3,
                    yerr=dat_xe, fmt='k.', alpha=0.2)
        f2.axhline(0, linestyle='--', color='r')
        f2.set_xlabel('Time (HJD)')
        f2.set_ylabel('Obs - Mod')

    plt.close(n_phot_sets + 3)
    fig = plt.figure(n_phot_sets + 3, figsize=(10, 10))  # PLOT 3
    fig_list.append(fig)
    plt.clf()
    plt.subplots_adjust(bottom=0.25, left=0.25)

    # Decide if we're plotting residuals
    if residuals:
        f1 = plt.gcf().add_axes([0.15, 0.3, 0.8, 0.6])
        f2 = plt.gcf().add_axes([0.15, 0.1, 0.8, 0.2])
    else:
        plt.gca()

    f1.errorbar(dat_t, dat_y, yerr=dat_ye, fmt='k.', label='Data')
    f1.plot(t_mod, pos_out[:, 1] * 1e3, 'r-', label='Model')
    if input_model != None:
        f1.plot(t_mod, pos_in[:, 1] * 1e3, 'g-', label='Input')
    f1.set_xlabel('t - t0 (days)')
    f1.set_ylabel(r'$\Delta \delta$ (mas)')
    f1.legend()

    # Decide if plotting traces
    if mnest_results is not None:
        for idx in np.arange(len(idx_arr)):
            f1.plot(t_mod, trace_posys[idx], 
                     color='c',
                     alpha=0.5,
                     linewidth=1,
                     zorder=-1)

    if residuals:
        f1.get_xaxis().set_visible(False)
        f1.get_shared_x_axes().join(f1, f2)
        f2.errorbar(dat_t,
                    dat_y - model.get_astrometry(dat_t, ast_filt_idx=ast_filt_index)[:,1] * 1e3,
                    yerr=dat_ye, fmt='k.', alpha=0.2)
        f2.axhline(0, linestyle='--', color='r')
        f2.set_xlabel('Time (HJD)')
        f2.set_ylabel('Obs - Mod')

    #####
    # Remove the unlensed motion (proper motion)
    # astrometry vs. time
    #####
    # Make the model unlensed points.
    p_mod_unlens_tdat = model.get_astrometry_unlensed(dat_t)
    x_mod_tdat = p_mod_unlens_tdat[:, 0]
    y_mod_tdat = p_mod_unlens_tdat[:, 1]
    x_no_pm = data['xpos' + str(filt_index + 1)] - x_mod_tdat
    y_no_pm = data['ypos' + str(filt_index + 1)] - y_mod_tdat

    # Make the dense sampled model for the same plot
    dp_tmod_unlens = model.get_astrometry(t_mod, ast_filt_idx=ast_filt_index) - model.get_astrometry_unlensed(t_mod)
    x_mod_no_pm = dp_tmod_unlens[:, 0]
    y_mod_no_pm = dp_tmod_unlens[:, 1]

    # Long time    
    baseline = np.max((2*(dat_t.max() - dat_t.min()),
                       5*model.tE))
    longtime = np.arange(model.t0-baseline, model.t0+baseline, 1)
    dp_tmod_unlens_longtime = model.get_astrometry(longtime) - model.get_astrometry_unlensed(longtime)
    x_mod_no_pm_longtime = dp_tmod_unlens_longtime[:, 0]
    y_mod_no_pm_longtime = dp_tmod_unlens_longtime[:, 1]

    # Make the dense sampled model for the same plot for INPUT model
    if input_model != None:
        dp_tmod_unlens_in = input_model.get_astrometry(t_mod, ast_filt_idx=ast_filt_index) - input_model.get_astrometry_unlensed(t_mod)
        x_mod_no_pm_in = dp_tmod_unlens_in[:, 0]
        y_mod_no_pm_in = dp_tmod_unlens_in[:, 1]

    if (x_no_pm.ndim == 2 and x_no_pm.shape[0] == 1):
        x_no_pm = x_no_pm.reshape(len(x_no_pm[0]))
        y_no_pm = y_no_pm.reshape(len(y_no_pm[0]))

    # Prep some colorbar stuff
    cmap = plt.cm.viridis
    norm = plt.Normalize(vmin=dat_t.min(), vmax=dat_t.max())
    smap = plt.cm.ScalarMappable(cmap=cmap, norm=norm)
    smap.set_array([])

    plt.close(n_phot_sets + 4)
    fig = plt.figure(n_phot_sets + 4, figsize=(10, 10))  # PLOT 4
    fig_list.append(fig)
    plt.clf()

    plt.errorbar(dat_t, x_no_pm * 1e3,
                 yerr=dat_xe, fmt='k.', label='Data')
    plt.plot(t_mod, x_mod_no_pm * 1e3, 'r-', label='Model')

    if mnest_results is not None:
        for idx in np.arange(len(idx_arr)):
            plt.plot(t_mod, trace_posxs_no_pm[idx],
                     color='c',
                     alpha=0.5,
                     linewidth=1,
                     zorder=-1)

    if input_model != None:
        plt.plot(t_mod, x_mod_no_pm_in * 1e3, 'g-', label='Input')
    plt.xlabel('t - t0 (days)')
    plt.ylabel(r'$\Delta \alpha^*$ (mas)')
    plt.legend()

    plt.close(n_phot_sets + 5)
    fig = plt.figure(n_phot_sets + 5, figsize=(10, 10))  # PLOT 5
    fig_list.append(fig)
    plt.clf()
    plt.errorbar(dat_t, y_no_pm * 1e3,
                 yerr=dat_ye, fmt='k.', label='Data')
    plt.plot(t_mod, y_mod_no_pm * 1e3, 'r-', label='Model')

    if mnest_results is not None:
        for idx in np.arange(len(idx_arr)):
            plt.plot(t_mod, trace_posys_no_pm[idx],
                     color='c',
                     alpha=0.5,
                     linewidth=1,
                     zorder=-1)

    if input_model != None:
        plt.plot(t_mod, y_mod_no_pm_in * 1e3, 'g-', label='Input')
    plt.xlabel('t - t0 (days)')
    plt.ylabel(r'$\Delta \delta$ (mas)')
    plt.legend()

    plt.close(n_phot_sets + 6)
    fig = plt.figure(n_phot_sets + 6)  # PLOT 6
    fig_list.append(fig)
    plt.clf()

    plt.scatter(x_no_pm * 1e3, y_no_pm * 1e3, c=dat_t,
                cmap=cmap, norm=norm, s=5)
    plt.errorbar(x_no_pm * 1e3, y_no_pm * 1e3,
                 xerr=dat_xe, yerr=dat_ye,
                 fmt='none', ecolor=smap.to_rgba(dat_t))
    plt.scatter(x_mod_no_pm * 1e3, y_mod_no_pm * 1e3, c=t_mod, cmap=cmap,
                norm=norm)

    if mnest_results is not None:
        for idx in np.arange(len(idx_arr)):
            plt.plot(trace_posxs_no_pm[idx], trace_posys_no_pm[idx],
                     color='c',
                     alpha=0.5,
                     linewidth=1,
                     zorder=-1)

    plt.gca().invert_xaxis()
    plt.axis('equal')
    plt.xlabel(r'$\Delta \alpha^*$ (mas)')
    plt.ylabel(r'$\Delta \delta$ (mas)')
    plt.colorbar()

    #####
    # Astrometry on the sky
    #####
    plt.close(n_phot_sets + 7)
    fig = plt.figure(n_phot_sets + 7, figsize=(10, 10))  # PLOT 7
    fig_list.append(fig)
    plt.clf()

    # Data
    plt.errorbar(dat_x, dat_y,
                 xerr=dat_xe, yerr=dat_ye,
                 fmt='k.', label='Data')

    # Decide if we sample the models at a denser time, or just the
    # same times as the measurements.
    if dense_time:
        # 1 day sampling over whole range
        t_mod = np.arange(dat_t.min(), dat_t.max(), 1)
    else:
        t_mod = data['t_ast']
    # Model - usually from fitter
    pos_out = model.get_astrometry(t_mod)
    baseline = np.max((2*(dat_t.max() - dat_t.min()),
                      5*model.tE))
    pos_out_unlens = model.get_astrometry_unlensed(np.arange(model.t0-baseline, model.t0+baseline, 1))
    plt.plot(pos_out[:, 0] * 1e3, pos_out[:, 1] * 1e3, 'r-', label='Model')
    plt.plot(pos_out_unlens[:, 0] * 1e3, pos_out_unlens[:, 1] * 1e3, 'b:', label='Model unlensed')

    # Input model
    if input_model != None:
        pos_in = input_model.get_astrometry(t_mod)
        plt.plot(pos_in[:, 0] * 1e3, pos_in[:, 1] * 1e3, 'g-', label='Input Model')

    if mnest_results is not None:
        for idx in np.arange(len(idx_arr)):
            plt.plot(trace_posxs[idx], trace_posys[idx],
                     color='c',
                     alpha=0.5,
                     linewidth=1,
                     zorder=-1)

    plt.gca().invert_xaxis()
    plt.xlabel(r'$\Delta \alpha^*$ (mas)')
    plt.ylabel(r'$\Delta \delta$ (mas)')
    plt.legend(fontsize=12)

    plt.close(n_phot_sets + 8)
    fig = plt.figure(n_phot_sets + 8, figsize=(10, 10))  # PLOT 8
    fig_list.append(fig)
    plt.clf()

    plt.errorbar(dat_t, x_no_pm * 1e3,
                 yerr=dat_xe, fmt='k.', label='Data')
    plt.plot(longtime, x_mod_no_pm_longtime * 1e3, 'r-', label='Model')
    plt.xlabel('t - t0 (days)')
    plt.ylabel(r'$\Delta \alpha^*$ (mas)')
    plt.legend()

    if mnest_results is not None:
        for idx in np.arange(len(idx_arr)):
            plt.plot(t_mod, trace_posxs_no_pm[idx],
                     color='c',
                     alpha=0.5,
                     linewidth=1,
                     zorder=-1)

    plt.close(n_phot_sets + 9)
    fig = plt.figure(n_phot_sets + 9, figsize=(10, 10))  # PLOT 9
    fig_list.append(fig)
    plt.clf()
    plt.errorbar(dat_t, y_no_pm * 1e3,
                 yerr=dat_ye, fmt='k.', label='Data')
    plt.plot(longtime, y_mod_no_pm_longtime * 1e3, 'r-', label='Model')
    plt.xlabel('t - t0 (days)')
    plt.ylabel(r'$\Delta \delta$ (mas)')
    plt.legend()

    if mnest_results is not None:
        for idx in np.arange(len(idx_arr)):
            plt.plot(t_mod, trace_posys_no_pm[idx],
                     color='c',
                     alpha=0.5,
                     linewidth=1,
                     zorder=-1)

    # Prep some colorbar stuff
    cmap = plt.cm.viridis
    norm = plt.Normalize(vmin=dat_t.min(), vmax=dat_t.max())
    smap = plt.cm.ScalarMappable(cmap=cmap, norm=norm)
    smap.set_array([])

    plt.close(n_phot_sets + 10)
    fig = plt.figure(n_phot_sets + 10)  # PLOT 10
    fig_list.append(fig)
    plt.clf()

    plt.scatter(x_no_pm * 1e3, y_no_pm * 1e3, c=dat_t,
                cmap=cmap, norm=norm, s=5)
    plt.errorbar(x_no_pm * 1e3, y_no_pm * 1e3,
                 xerr=dat_xe, yerr=dat_ye,
                 fmt='none', ecolor=smap.to_rgba(dat_t))
    plt.colorbar()
    plt.scatter(x_mod_no_pm_longtime * 1e3, y_mod_no_pm_longtime * 1e3, s=1)
#                c=longtime, cmap=cmap, norm=norm, s=1)

    if mnest_results is not None:
        for idx in np.arange(len(idx_arr)):
            plt.plot(trace_posys_no_pm[idx], trace_posxs_no_pm[idx],
                     color='c',
                     alpha=0.5,
                     linewidth=1,
                     zorder=-1)

    plt.gca().invert_xaxis()
    plt.axis('equal')
    plt.xlabel(r'$\Delta \alpha^*$ (mas)')
    plt.ylabel(r'$\Delta \delta$ (mas)')

    return fig_list


def quantiles(mnest_results, sigma=1):
    """
    Calculate the median and N sigma credicble interval.

    Inputs
    ----------
    mnest_results : astropy table
        The table that comes out of load_mnest_results.

    Optional Inputs
    ----------
    sigma : int
        1, 2, or 3 sigma to determine which credible interval
        to return.
    """

    pars = mnest_results.colnames

    weights = mnest_results['weights']
    sumweights = np.sum(weights)
    weights = weights / sumweights

    sigmas = {1: 0.682689,
              2: 0.9545,
              3: 0.9973}

    sig = sigmas[sigma]
    sig_lo = (1.0 - sig) / 2.0
    sig_hi = 1.0 - sig_lo

    # Calculate the median and quantiles.
    med_vals = {}
    for n in pars:
        # Calculate median, sigma lo, and sigma hi credible interval.
        med_vals[n] = weighted_quantile(mnest_results[n],
                                        [0.5, sig_lo, sig_hi],
                                        sample_weight=weights)
        # Switch from values to errors.
        med_vals[n][1] = med_vals[n][0] - med_vals[n][1]
        med_vals[n][2] = med_vals[n][2] - med_vals[n][0]

    return pars, med_vals


def get_mnest_results(root_name, parameters):
    """
    Inputs
    ----------
    root_name : str
        The directory and base name of the MultiNest output.

    parameters : list or array
        A list of strings with the parameter names to be displayed.
        There should be one name for each parameter in MultiNest and
        in the order that they appeared in the hyper-cube.
    """
    prefix = root_name
    print('model "%s"' % prefix)

    n_params = len(parameters)

    a = pymultinest.Analyzer(n_params=n_params, outputfiles_basename=prefix)
    s = a.get_stats()

    json.dump(s, open(prefix + 'stats.json', 'w'), indent=4)

    print('  marginal likelihood:')
    print('    ln Z = %.1f +- %.1f' % (
        s['global evidence'], s['global evidence error']))
    print('  parameters:')
    for p, m in zip(parameters, s['marginals']):
        lo, hi = m['1sigma']
        med = m['median']
        sigma = (hi - lo) / 2
        if sigma == 0:
            i = 3
        else:
            i = max(0, int(-np.floor(np.log10(sigma))) + 1)
        fmt = '%%.%df' % i
        fmts = '\t'.join(['    %-15s' + fmt + " +- " + fmt])
        print(fmts % (p, med, sigma))

    data = a.get_data()
    i = data[:, 1].argsort()[::-1]
    samples = data[i, 2:]
    weights = data[i, 0]
    loglike = data[i, 1]
    Z = s['global evidence']
    logvol = np.log(weights) + 0.5 * loglike + Z
    logvol = logvol - logvol.max()

    results = dict(samples=samples, weights=weights, logvol=logvol)

    return results


def calc_AIC(k, maxlogL):
    """
    Calculate Akaike Information Criterion.
    k = number of parameters
    maxlogL = maximum log likelihood
    """
    aic = 2 * (k - maxlogL)

    return aic


def calc_BIC(n, k, maxlogL):
    """
    Calculate Bayesian Information Criterion.
    n = sample size
    k = number of parameters
    maxlogL = maximum log likelihood
    """
    bic = np.log(n) * k - 2 * maxlogL

    return bic

# Custom dynesty plotting.
def postplot(results, span=None, quantiles=[0.025, 0.5, 0.975], q_color = 'gray', smooth=0.02,
             post_color='blue', post_kwargs=None, kde=True, nkde=1000,
             max_n_ticks=5, use_math_text=False,
             labels=None, label_kwargs=None,
             show_titles=False, title_fmt=".2f", title_kwargs=None,
             truths1=None, truths2=None, truth_color1='red', truth_color2='blue',
             truth_kwargs1=None, truth_kwargs2=None, 
             verbose=False, fig=None):
    """
    Plot marginalized posteriors for each parameter.
    Basically copied half of traceplot.

    Parameters
    ----------
    results : :class:`~dynesty.results.Results` instance
        A :class:`~dynesty.results.Results` instance from a nested
        sampling run. **Compatible with results derived from**
        `nestle <http://kylebarbary.com/nestle/>`_.

    span : iterable with shape (ndim,), optional
        A list where each element is either a length-2 tuple containing
        lower and upper bounds or a float from `(0., 1.]` giving the
        fraction of (weighted) samples to include. If a fraction is provided,
        the bounds are chosen to be equal-tailed. An example would be::

            span = [(0., 10.), 0.95, (5., 6.)]

        Default is `0.999999426697` (5-sigma credible interval) for each
        parameter.

    quantiles : iterable, optional
        A list of fractional quantiles to overplot on the 1-D marginalized
        posteriors as vertical dashed lines. Default is `[0.025, 0.5, 0.975]`
        (the 95%/2-sigma credible interval).

    smooth : float or iterable with shape (ndim,), optional
        The standard deviation (either a single value or a different value for
        each subplot) for the Gaussian kernel used to smooth the 1-D
        marginalized posteriors, expressed as a fraction of the span.
        Default is `0.02` (2% smoothing). If an integer is provided instead,
        this will instead default to a simple (weighted) histogram with
        `bins=smooth`.

    post_color : str or iterable with shape (ndim,), optional
        A `~matplotlib`-style color (either a single color or a different
        value for each subplot) used when plotting the histograms.
        Default is `'blue'`.

    post_kwargs : dict, optional
        Extra keyword arguments that will be used for plotting the
        marginalized 1-D posteriors.

    kde : bool, optional
        Whether to use kernel density estimation to estimate and plot
        the PDF of the importance weights as a function of log-volume
        (as opposed to the importance weights themselves). Default is
        `True`.

    nkde : int, optional
        The number of grid points used when plotting the kernel density
        estimate. Default is `1000`.

    max_n_ticks : int, optional
        Maximum number of ticks allowed. Default is `5`.

    use_math_text : bool, optional
        Whether the axis tick labels for very large/small exponents should be
        displayed as powers of 10 rather than using `e`. Default is `False`.

    labels : iterable with shape (ndim,), optional
        A list of names for each parameter. If not provided, the default name
        used when plotting will follow :math:`x_i` style.

    label_kwargs : dict, optional
        Extra keyword arguments that will be sent to the
        `~matplotlib.axes.Axes.set_xlabel` and
        `~matplotlib.axes.Axes.set_ylabel` methods.

    show_titles : bool, optional
        Whether to display a title above each 1-D marginalized posterior
        showing the 0.5 quantile along with the upper/lower bounds associated
        with the 0.025 and 0.975 (95%/2-sigma credible interval) quantiles.
        Default is `True`.

    title_fmt : str, optional
        The format string for the quantiles provided in the title. Default is
        `'.2f'`.

    title_kwargs : dict, optional
        Extra keyword arguments that will be sent to the
        `~matplotlib.axes.Axes.set_title` command.

    truths : iterable with shape (ndim,), optional
        A list of reference values that will be overplotted on the traces and
        marginalized 1-D posteriors as solid horizontal/vertical lines.
        Individual values can be exempt using `None`. Default is `None`.

    truth_color : str or iterable with shape (ndim,), optional
        A `~matplotlib`-style color (either a single color or a different
        value for each subplot) used when plotting `truths`.
        Default is `'red'`.

    truth_kwargs : dict, optional
        Extra keyword arguments that will be used for plotting the vertical
        and horizontal lines with `truths`.

    verbose : bool, optional
        Whether to print the values of the computed quantiles associated with
        each parameter. Default is `False`.

    fig : (`~matplotlib.figure.Figure`, `~matplotlib.axes.Axes`), optional
        If provided, overplot the traces and marginalized 1-D posteriors
        onto the provided figure. Otherwise, by default an
        internal figure is generated.

    Returns
    -------
    traceplot : (`~matplotlib.figure.Figure`, `~matplotlib.axes.Axes`)
        Output trace plot.

    """

    # Initialize values.
    if title_kwargs is None:
        title_kwargs = dict()
    if label_kwargs is None:
        label_kwargs = dict()
    if post_kwargs is None:
        post_kwargs = dict()
    if truth_kwargs1 is None:
        truth_kwargs1 = dict()
    if truth_kwargs2 is None:
        truth_kwargs2 = dict()

    # Set defaults.
    post_kwargs['alpha'] = post_kwargs.get('alpha', 0.6)
    truth_kwargs1['linestyle'] = truth_kwargs1.get('linestyle', 'solid')
    truth_kwargs1['linewidth'] = truth_kwargs1.get('linewidth', 2)
    truth_kwargs1['alpha'] = truth_kwargs1.get('alpha', 0.7)
    truth_kwargs2['linestyle'] = truth_kwargs2.get('linestyle', 'dashed')
    truth_kwargs2['linewidth'] = truth_kwargs2.get('linewidth', 2)
    truth_kwargs2['alpha'] = truth_kwargs2.get('alpha', 0.7)

    # Extract weighted samples.
    samples = results['samples']
    logvol = results['logvol']
    try:
        weights = np.exp(results['logwt'] - results['logz'][-1])
    except:
        weights = results['weights']
    if kde:
        # Derive kernel density estimate.
        wt_kde = gaussian_kde(resample_equal(-logvol, weights))  # KDE
        logvol_grid = np.linspace(logvol[0], logvol[-1], nkde)  # resample
        wt_grid = wt_kde.pdf(-logvol_grid)  # evaluate KDE PDF
        wts = np.interp(-logvol, -logvol_grid, wt_grid)  # interpolate
    else:
        wts = weights

    # Deal with 1D results. A number of extra catches are also here
    # in case users are trying to plot other results besides the `Results`
    # instance generated by `dynesty`.
    samples = np.atleast_1d(samples)
    if len(samples.shape) == 1:
        samples = np.atleast_2d(samples)
    else:
        assert len(samples.shape) == 2, "Samples must be 1- or 2-D."
        samples = samples.T
    assert samples.shape[0] <= samples.shape[1], "There are more " \
                                                 "dimensions than samples!"
    ndim, nsamps = samples.shape

    # Check weights.
    if weights.ndim != 1:
        raise ValueError("Weights must be 1-D.")
    if nsamps != weights.shape[0]:
        raise ValueError("The number of weights and samples disagree!")

    # Check ln(volume).
    if logvol.ndim != 1:
        raise ValueError("Ln(volume)'s must be 1-D.")
    if nsamps != logvol.shape[0]:
        raise ValueError("The number of ln(volume)'s and samples disagree!")

    # Determine plotting bounds for marginalized 1-D posteriors.
    if span is None:
        span = [0.999999426697 for i in range(ndim)]
    span = list(span)
    if len(span) != ndim:
        raise ValueError("Dimension mismatch between samples and span.")
    for i, _ in enumerate(span):
        try:
            xmin, xmax = span[i]
        except:
            q = [0.5 - 0.5 * span[i], 0.5 + 0.5 * span[i]]
            span[i] = _quantile(samples[i], q, weights=weights)

    # Setting up labels.
    if labels is None:
        labels = [r"$x_{"+str(i+1)+"}$" for i in range(ndim)]

    # Setting up smoothing.
    if (isinstance(smooth, int_type) or isinstance(smooth, float_type)):
        smooth = [smooth for i in range(ndim)]
    
    # Setting up default plot layout.
    if fig is None:
        fig, axes = plt.subplots(ndim, 1, figsize=(6, 2.5*ndim))
    else:
        fig, axes = fig
        try:
            axes.reshape(ndim, 1)
        except:
            raise ValueError("Provided axes do not match the required shape "
                             "for plotting samples.")

    # Format figure.
    fig.subplots_adjust(bottom=0.05, top=0.95, 
                        left = 0.1, right = 0.9, 
                        hspace=0.7)

    # Plot marginalized 1-D posterior.
    for i, x in enumerate(samples):
        # Establish axes.
        ax = axes[i]

        # Set color(s).
        if isinstance(post_color, str_type):
            color = post_color
        else:
            color = post_color[i]
        # Setup axes
        ax.set_xlim(span[i])
        if max_n_ticks == 0:
            ax.xaxis.set_major_locator(NullLocator())
            ax.yaxis.set_major_locator(NullLocator())
        else:
            ax.xaxis.set_major_locator(MaxNLocator(max_n_ticks))
            ax.yaxis.set_major_locator(NullLocator())
        # Label axes.
        sf = ScalarFormatter(useMathText=use_math_text)
        ax.xaxis.set_major_formatter(sf)
        ax.set_xlabel(labels[i], **label_kwargs)
        # Generate distribution.
        s = smooth[i]
        if isinstance(s, int_type):
            # If `s` is an integer, plot a weighted histogram with
            # `s` bins within the provided bounds.
            n, b, _ = ax.hist(x, bins=s, weights=weights, color=color,
                              range=np.sort(span[i]), **post_kwargs)
            x0 = np.array(list(zip(b[:-1], b[1:]))).flatten()
            y0 = np.array(list(zip(n, n))).flatten()
        else:
            # If `s` is a float, oversample the data relative to the
            # smoothing filter by a factor of 10, then use a Gaussian
            # filter to smooth the results.
            bins = int(round(10. / s))
            n, b = np.histogram(x, bins=bins, weights=weights,
                                range=np.sort(span[i]))
            n = norm_kde(n, 10.)
            x0 = 0.5 * (b[1:] + b[:-1])
            y0 = n
            ax.fill_between(x0, y0, color=color, **post_kwargs)
        ax.set_ylim([0., max(y0) * 1.05])
        # Plot quantiles.
        if quantiles is not None and len(quantiles) > 0:
            qs = _quantile(x, quantiles, weights=weights)
            for q in qs:
                ax.axvline(q, lw=2, ls=":", color=q_color)
            if verbose:
                print("Quantiles:")
                print(labels[i], [blob for blob in zip(quantiles, qs)])
        # Add truth value(s) for Mode 1.
        if truths1 is not None and truths1[i] is not None:
            try:
                [ax.axvline(t, color=truth_color1, **truth_kwargs1)
                 for t in truths1[i]]
            except:
                ax.axvline(truths1[i], color=truth_color1, **truth_kwargs1)
        # Add truth value(s) for Mode 2.
        if truths2 is not None and truths2[i] is not None:
            try:
                [ax.axvline(t, color=truth_color2, **truth_kwargs2)
                 for t in truths2[i]]
            except:
                ax.axvline(truths2[i], color=truth_color2, **truth_kwargs2)
        # Set titles.
        if show_titles:
            title = None
            if title_fmt is not None:
                ql, qm, qh = _quantile(x, [0.025, 0.5, 0.975], weights=weights)
                q_minus, q_plus = qm - ql, qh - qm
                fmt = "{{0:{0}}}".format(title_fmt).format
                title = r"${{{0}}}_{{-{1}}}^{{+{2}}}$"
                title = title.format(fmt(qm), fmt(q_minus), fmt(q_plus))
                title = "{0} = {1}".format(labels[i], title)
                ax.set_title(title, **title_kwargs)

    return fig, axes


def cornerplot_2truth(results, dims=None, span=None, quantiles=[0.025, 0.5, 0.975],
                      color='black', smooth=0.02, quantiles_2d=None, hist_kwargs=None,
                      hist2d_kwargs=None, labels=None, label_kwargs=None,
                      show_titles=False, title_fmt=".2f", title_kwargs=None,
                      truths1=None, truth_color1='red', truth_kwargs1=None,
                      truths2=None, truth_color2='blue', truth_kwargs2=None,
                      max_n_ticks=5, top_ticks=False, use_math_text=False,
                      verbose=False, fig=None):
    """
    Generate a corner plot of the 1-D and 2-D marginalized posteriors.

    Parameters
    ----------
    results : :class:`~dynesty.results.Results` instance
        A :class:`~dynesty.results.Results` instance from a nested
        sampling run. **Compatible with results derived from**
        `nestle <http://kylebarbary.com/nestle/>`_.

    dims : iterable of shape (ndim,), optional
        The subset of dimensions that should be plotted. If not provided,
        all dimensions will be shown.

    span : iterable with shape (ndim,), optional
        A list where each element is either a length-2 tuple containing
        lower and upper bounds or a float from `(0., 1.]` giving the
        fraction of (weighted) samples to include. If a fraction is provided,
        the bounds are chosen to be equal-tailed. An example would be::

            span = [(0., 10.), 0.95, (5., 6.)]

        Default is `0.999999426697` (5-sigma credible interval).

    quantiles : iterable, optional
        A list of fractional quantiles to overplot on the 1-D marginalized
        posteriors as vertical dashed lines. Default is `[0.025, 0.5, 0.975]`
        (spanning the 95%/2-sigma credible interval).

    color : str or iterable with shape (ndim,), optional
        A `~matplotlib`-style color (either a single color or a different
        value for each subplot) used when plotting the histograms.
        Default is `'black'`.

    smooth : float or iterable with shape (ndim,), optional
        The standard deviation (either a single value or a different value for
        each subplot) for the Gaussian kernel used to smooth the 1-D and 2-D
        marginalized posteriors, expressed as a fraction of the span.
        Default is `0.02` (2% smoothing). If an integer is provided instead,
        this will instead default to a simple (weighted) histogram with
        `bins=smooth`.

    quantiles_2d : iterable with shape (nquant,), optional
        The quantiles used for plotting the smoothed 2-D distributions.
        If not provided, these default to 0.5, 1, 1.5, and 2-sigma contours
        roughly corresponding to quantiles of `[0.1, 0.4, 0.65, 0.85]`.

    hist_kwargs : dict, optional
        Extra keyword arguments to send to the 1-D (smoothed) histograms.

    hist2d_kwargs : dict, optional
        Extra keyword arguments to send to the 2-D (smoothed) histograms.

    labels : iterable with shape (ndim,), optional
        A list of names for each parameter. If not provided, the default name
        used when plotting will follow :math:`x_i` style.

    label_kwargs : dict, optional
        Extra keyword arguments that will be sent to the
        `~matplotlib.axes.Axes.set_xlabel` and
        `~matplotlib.axes.Axes.set_ylabel` methods.

    show_titles : bool, optional
        Whether to display a title above each 1-D marginalized posterior
        showing the 0.5 quantile along with the upper/lower bounds associated
        with the 0.025 and 0.975 (95%/2-sigma credible interval) quantiles.
        Default is `True`.

    title_fmt : str, optional
        The format string for the quantiles provided in the title. Default is
        `'.2f'`.

    title_kwargs : dict, optional
        Extra keyword arguments that will be sent to the
        `~matplotlib.axes.Axes.set_title` command.

    truths : iterable with shape (ndim,), optional
        A list of reference values that will be overplotted on the traces and
        marginalized 1-D posteriors as solid horizontal/vertical lines.
        Individual values can be exempt using `None`. Default is `None`.

    truth_color : str or iterable with shape (ndim,), optional
        A `~matplotlib`-style color (either a single color or a different
        value for each subplot) used when plotting `truths`.
        Default is `'red'`.

    truth_kwargs : dict, optional
        Extra keyword arguments that will be used for plotting the vertical
        and horizontal lines with `truths`.

    max_n_ticks : int, optional
        Maximum number of ticks allowed. Default is `5`.

    top_ticks : bool, optional
        Whether to label the top (rather than bottom) ticks. Default is
        `False`.

    use_math_text : bool, optional
        Whether the axis tick labels for very large/small exponents should be
        displayed as powers of 10 rather than using `e`. Default is `False`.

    verbose : bool, optional
        Whether to print the values of the computed quantiles associated with
        each parameter. Default is `False`.

    fig : (`~matplotlib.figure.Figure`, `~matplotlib.axes.Axes`), optional
        If provided, overplot the traces and marginalized 1-D posteriors
        onto the provided figure. Otherwise, by default an
        internal figure is generated.

    Returns
    -------
    cornerplot : (`~matplotlib.figure.Figure`, `~matplotlib.axes.Axes`)
        Output corner plot.

    """

    # Initialize values.
    if quantiles is None:
        quantiles = []
    if truth_kwargs1 is None:
        truth_kwargs1 = dict()
    if truth_kwargs2 is None:
        truth_kwargs2 = dict()
    if label_kwargs is None:
        label_kwargs = dict()
    if title_kwargs is None:
        title_kwargs = dict()
    if hist_kwargs is None:
        hist_kwargs = dict()
    if hist2d_kwargs is None:
        hist2d_kwargs = dict()

    # Set defaults.
    hist_kwargs['alpha'] = hist_kwargs.get('alpha', 0.6)
    hist2d_kwargs['alpha'] = hist2d_kwargs.get('alpha', 0.6)
    hist2d_kwargs['levels'] = hist2d_kwargs.get('levels', quantiles_2d)
    truth_kwargs1['linestyle'] = truth_kwargs1.get('linestyle', 'solid')
    truth_kwargs1['linewidth'] = truth_kwargs1.get('linewidth', 2)
    truth_kwargs1['alpha'] = truth_kwargs1.get('alpha', 0.7)
    truth_kwargs2['linestyle'] = truth_kwargs2.get('linestyle', 'dashed')
    truth_kwargs2['linewidth'] = truth_kwargs2.get('linewidth', 2)
    truth_kwargs2['alpha'] = truth_kwargs2.get('alpha', 0.7)

    # Extract weighted samples.
    samples = results['samples']
    try:
        weights = np.exp(results['logwt'] - results['logz'][-1])
    except:
        weights = results['weights']

    # Deal with 1D results. A number of extra catches are also here
    # in case users are trying to plot other results besides the `Results`
    # instance generated by `dynesty`.
    samples = np.atleast_1d(samples)
    if len(samples.shape) == 1:
        samples = np.atleast_2d(samples)
    else:
        assert len(samples.shape) == 2, "Samples must be 1- or 2-D."
        samples = samples.T
    assert samples.shape[0] <= samples.shape[1], "There are more " \
                                                 "dimensions than samples!"

    # Slice samples based on provided `dims`.
    if dims is not None:
        samples = samples[dims]
    ndim, nsamps = samples.shape

    # Check weights.
    if weights.ndim != 1:
        raise ValueError("Weights must be 1-D.")
    if nsamps != weights.shape[0]:
        raise ValueError("The number of weights and samples disagree!")

    # Determine plotting bounds.
    if span is None:
        span = [0.999999426697 for i in range(ndim)]
    span = list(span)
    if len(span) != ndim:
        raise ValueError("Dimension mismatch between samples and span.")
    for i, _ in enumerate(span):
        try:
            xmin, xmax = span[i]
        except:
            q = [0.5 - 0.5 * span[i], 0.5 + 0.5 * span[i]]
            span[i] = _quantile(samples[i], q, weights=weights)

    # Set labels
    if labels is None:
        labels = [r"$x_{"+str(i+1)+"}$" for i in range(ndim)]

    # Setting up smoothing.
    if (isinstance(smooth, int_type) or isinstance(smooth, float_type)):
        smooth = [smooth for i in range(ndim)]

    # Setup axis layout (from `corner.py`).
    factor = 2.0  # size of side of one panel
    lbdim = 0.5 * factor  # size of left/bottom margin
    trdim = 0.2 * factor  # size of top/right margin
    whspace = 0.05  # size of width/height margin
    plotdim = factor * ndim + factor * (ndim - 1.) * whspace  # plot size
    dim = lbdim + plotdim + trdim  # total size

    # Initialize figure.
    if fig is None:
        fig, axes = plt.subplots(ndim, ndim, figsize=(dim, dim))
    else:
        try:
            fig, axes = fig
            axes = np.array(axes).reshape((ndim, ndim))
        except:
            raise ValueError("Mismatch between axes and dimension.")

    # Format figure.
    lb = lbdim / dim
    tr = (lbdim + plotdim) / dim
    fig.subplots_adjust(left=lb, bottom=lb, right=tr, top=tr,
                        wspace=whspace, hspace=whspace)

    # Plotting.
    for i, x in enumerate(samples):
        if np.shape(samples)[0] == 1:
            ax = axes
        else:
            ax = axes[i, i]

        # Plot the 1-D marginalized posteriors.

        # Setup axes
        ax.set_xlim(span[i])
        if max_n_ticks == 0:
            ax.xaxis.set_major_locator(NullLocator())
            ax.yaxis.set_major_locator(NullLocator())
        else:
            ax.xaxis.set_major_locator(MaxNLocator(max_n_ticks,
                                                   prune="lower"))
            ax.yaxis.set_major_locator(NullLocator())
        # Label axes.
        sf = ScalarFormatter(useMathText=use_math_text)
        ax.xaxis.set_major_formatter(sf)
        if i < ndim - 1:
            if top_ticks:
                ax.xaxis.set_ticks_position("top")
                [l.set_rotation(45) for l in ax.get_xticklabels()]
            else:
                ax.set_xticklabels([])
        else:
            [l.set_rotation(45) for l in ax.get_xticklabels()]
            ax.set_xlabel(labels[i], **label_kwargs)
            ax.xaxis.set_label_coords(0.5, -0.3)
        # Generate distribution.
        sx = smooth[i]
        if isinstance(sx, int_type):
            # If `sx` is an integer, plot a weighted histogram with
            # `sx` bins within the provided bounds.
            n, b, _ = ax.hist(x, bins=sx, weights=weights, color=color,
                              range=np.sort(span[i]), **hist_kwargs)
        else:
            # If `sx` is a float, oversample the data relative to the
            # smoothing filter by a factor of 10, then use a Gaussian
            # filter to smooth the results.
            bins = int(round(10. / sx))
            n, b = np.histogram(x, bins=bins, weights=weights,
                                range=np.sort(span[i]))
            n = norm_kde(n, 10.)
            b0 = 0.5 * (b[1:] + b[:-1])
            n, b, _ = ax.hist(b0, bins=b, weights=n,
                              range=np.sort(span[i]), color=color,
                              **hist_kwargs)
        ax.set_ylim([0., max(n) * 1.05])
        # Plot quantiles.
        if quantiles is not None and len(quantiles) > 0:
            qs = _quantile(x, quantiles, weights=weights)
            for q in qs:
                ax.axvline(q, lw=2, ls="dashed", color=color)
            if verbose:
                print("Quantiles:")
                print(labels[i], [blob for blob in zip(quantiles, qs)])
        # Add truth value(s) for mode 1.
        if truths1 is not None and truths1[i] is not None:
            try:
                [ax.axvline(t, color=truth_color1, **truth_kwargs1)
                 for t in truths1[i]]
            except:
                ax.axvline(truths1[i], color=truth_color1, **truth_kwargs1)
        # Add truth value(s) for mode 2.
        if truths2 is not None and truths2[i] is not None:
            try:
                [ax.axvline(t, color=truth_color2, **truth_kwargs2)
                 for t in truths2[i]]
            except:
                ax.axvline(truths2[i], color=truth_color2, **truth_kwargs2)
        # Set titles.
        if show_titles:
            title = None
            if title_fmt is not None:
                ql, qm, qh = _quantile(x, [0.025, 0.5, 0.975], weights=weights)
                q_minus, q_plus = qm - ql, qh - qm
                fmt = "{{0:{0}}}".format(title_fmt).format
                title = r"${{{0}}}_{{-{1}}}^{{+{2}}}$"
                title = title.format(fmt(qm), fmt(q_minus), fmt(q_plus))
                title = "{0} = {1}".format(labels[i], title)
                ax.set_title(title, **title_kwargs)

        for j, y in enumerate(samples):
            if np.shape(samples)[0] == 1:
                ax = axes
            else:
                ax = axes[i, j]

            # Plot the 2-D marginalized posteriors.

            # Setup axes.
            if j > i:
                ax.set_frame_on(False)
                ax.set_xticks([])
                ax.set_yticks([])
                continue
            elif j == i:
                continue

            if max_n_ticks == 0:
                ax.xaxis.set_major_locator(NullLocator())
                ax.yaxis.set_major_locator(NullLocator())
            else:
                ax.xaxis.set_major_locator(MaxNLocator(max_n_ticks,
                                                       prune="lower"))
                ax.yaxis.set_major_locator(MaxNLocator(max_n_ticks,
                                                       prune="lower"))
            # Label axes.
            sf = ScalarFormatter(useMathText=use_math_text)
            ax.xaxis.set_major_formatter(sf)
            ax.yaxis.set_major_formatter(sf)
            if i < ndim - 1:
                ax.set_xticklabels([])
            else:
                [l.set_rotation(45) for l in ax.get_xticklabels()]
                ax.set_xlabel(labels[j], **label_kwargs)
                ax.xaxis.set_label_coords(0.5, -0.3)
            if j > 0:
                ax.set_yticklabels([])
            else:
                [l.set_rotation(45) for l in ax.get_yticklabels()]
                ax.set_ylabel(labels[i], **label_kwargs)
                ax.yaxis.set_label_coords(-0.3, 0.5)
            # Generate distribution.
            sy = smooth[j]
            check_ix = isinstance(sx, int_type)
            check_iy = isinstance(sy, int_type)
            if check_ix and check_iy:
                fill_contours = False
                plot_contours = False
            else:
                fill_contours = True
                plot_contours = True
            hist2d_kwargs['fill_contours'] = hist2d_kwargs.get('fill_contours',
                                                               fill_contours)
            hist2d_kwargs['plot_contours'] = hist2d_kwargs.get('plot_contours',
                                                               plot_contours)
            dyplot._hist2d(y, x, ax=ax, span=[span[j], span[i]],
                    weights=weights, color=color, smooth=[sy, sx],
                    **hist2d_kwargs)
            # Add truth values for mode 1.
            if truths1 is not None:
                if truths1[j] is not None:
                    try:
                        [ax.axvline(t, color=truth_color1, **truth_kwargs1)
                         for t in truths[j]]
                    except:
                        ax.axvline(truths1[j], color=truth_color1,
                                   **truth_kwargs1)
                if truths1[i] is not None:
                    try:
                        [ax.axhline(t, color=truth_color1, **truth_kwargs1)
                         for t in truths1[i]]
                    except:
                        ax.axhline(truths1[i], color=truth_color1,
                                   **truth_kwargs1)
            # Add truth values for mode 2.
            if truths2 is not None:
                if truths2[j] is not None:
                    try:
                        [ax.axvline(t, color=truth_color2, **truth_kwargs2)
                         for t in truths2[j]]
                    except:
                        ax.axvline(truths2[j], color=truth_color2,
                                   **truth_kwargs2)
                if truths2[i] is not None:
                    try:
                        [ax.axhline(t, color=truth_color2, **truth_kwargs2)
                         for t in truths2[i]]
                    except:
                        ax.axhline(truths2[i], color=truth_color2,
                                   **truth_kwargs2)

    return (fig, axes)

def contour2d_alpha(x, y, smooth=0.02, span=None, weights=None, sigma_levels=[1, 2, 3],
                    ax=None, color='gray', 
                    plot_density=True,
                    plot_contours=True, 
                    contour_kwargs=None, 
                    **kwargs):
    """
    Simplified/modified from dynesty's plotting._hist2d function.
    Plots non-filled 2D contours, where the contours are the 
    0.5, 1, 1.5, 2 sigma contours (note this)

    Parameters
    ----------
    x : interable with shape (nsamps,)
       Sample positions in the first dimension.

    y : iterable with shape (nsamps,)
       Sample positions in the second dimension.

    span : iterable with shape (ndim,), optional
        A list where each element is either a length-2 tuple containing
        lower and upper bounds or a float from `(0., 1.]` giving the
        fraction of (weighted) samples to include. If a fraction is provided,
        the bounds are chosen to be equal-tailed. An example would be::

            span = [(0., 10.), 0.95, (5., 6.)]

        Default is `0.999999426697` (5-sigma credible interval).

    weights : iterable with shape (nsamps,)
        Weights associated with the samples. Default is `None` (no weights).

    sigma_levels : iterable, optional
        The contour levels to draw. Default are `[1, 2, 3]`-sigma.
        UNITS ARE IN SIGMA

    ax : `~matplotlib.axes.Axes`, optional
        An `~matplotlib.axes.axes` instance on which to add the 2-D histogram.
        If not provided, a figure will be generated.

    color : str, optional
        The `~matplotlib`-style color used to draw lines and color cells
        and contours. Default is `'gray'`.

    plot_density : bool, optional
        Whether to draw the density colormap. Default is `True`.

    plot_contours : bool, optional
        Whether to draw the contours. Default is `True`.

    contour_kwargs : dict
        Any additional keyword arguments to pass to the `contour` method.

    data_kwargs : dict
        Any additional keyword arguments to pass to the `plot` method when
        adding the individual data points.

    """
    if ax is None:
        ax = plt.gca()

    # Determine plotting bounds.
    data = [x, y]
    if span is None:
        span = [0.999999426697 for i in range(2)]
    span = list(span)
    if len(span) != 2:
        raise ValueError("Dimension mismatch between samples and span.")
    for i, _ in enumerate(span):
        try:
            xmin, xmax = span[i]
        except:
            q = [0.5 - 0.5 * span[i], 0.5 + 0.5 * span[i]]
            span[i] = _quantile(data[i], q, weights=weights)

    # Get the contour levels
    levels = []
    for sigma in sigma_levels:
        level = 1.0 - np.exp(-0.5 * np.array([sigma]) ** 2)
        levels.append(level)
                              
    # Color map for the density plot, over-plotted to indicate the
    # density of the points near the center.
    density_cmap = LinearSegmentedColormap.from_list(
        "density_cmap", [color, (1, 1, 1, 0)])

    # Color map used to hide the points at the high density areas.
    white_cmap = LinearSegmentedColormap.from_list(
        "white_cmap", [(1, 1, 1), (1, 1, 1)], N=2)

    # Initialize smoothing.
    if (isinstance(smooth, int_type) or isinstance(smooth, float_type)):
        smooth = [smooth, smooth]
    bins = []
    svalues = []
    for s in smooth:
        if isinstance(s, int_type):
            # If `s` is an integer, the weighted histogram has
            # `s` bins within the provided bounds.
            bins.append(s)
            svalues.append(0.)
        else:
            # If `s` is a float, oversample the data relative to the
            # smoothing filter by a factor of 2, then use a Gaussian
            # filter to smooth the results.
            bins.append(int(round(2. / s)))
            svalues.append(2.)

    # We'll make the 2D histogram to directly estimate the density.
    try:
        H, X, Y = np.histogram2d(x.flatten(), y.flatten(), bins=bins,
                                 range=list(map(np.sort, span)),
                                 weights=weights)
    except ValueError:
        raise ValueError("It looks like at least one of your sample columns "
                         "have no dynamic range.")

    # Smooth the results.
    if not np.all(svalues == 0.):
        H = norm_kde(H, svalues)

    # Compute the density levels.
    Hflat = H.flatten()
    inds = np.argsort(Hflat)[::-1]
    Hflat = Hflat[inds]
    sm = np.cumsum(Hflat)
    sm /= sm[-1]

    Vs = []
    for level in levels:
        V = np.empty(len(level))
        for i, v0 in enumerate(level):
            try:
                V[i] = Hflat[sm <= v0][-1]
            except:
                V[i] = Hflat[0]
        V.sort()
        m = (np.diff(V) == 0)
        if np.any(m) and plot_contours:
            logging.warning("Too few points to create valid contours.")
        while np.any(m):
            V[np.where(m)[0][0]] *= 1.0 - 1e-4
            m = (np.diff(V) == 0)
        V.sort()        
        Vs.append(V)

    # Compute the bin centers.
    X1, Y1 = 0.5 * (X[1:] + X[:-1]), 0.5 * (Y[1:] + Y[:-1])

    # Extend the array for the sake of the contours at the plot edges.
    H2 = H.min() + np.zeros((H.shape[0] + 4, H.shape[1] + 4))
    H2[2:-2, 2:-2] = H
    H2[2:-2, 1] = H[:, 0]
    H2[2:-2, -2] = H[:, -1]
    H2[1, 2:-2] = H[0]
    H2[-2, 2:-2] = H[-1]
    H2[1, 1] = H[0, 0]
    H2[1, -2] = H[0, -1]
    H2[-2, 1] = H[-1, 0]
    H2[-2, -2] = H[-1, -1]
    X2 = np.concatenate([X1[0] + np.array([-2, -1]) * np.diff(X1[:2]), X1,
                         X1[-1] + np.array([1, 2]) * np.diff(X1[-2:])])
    Y2 = np.concatenate([Y1[0] + np.array([-2, -1]) * np.diff(Y1[:2]), Y1,
                         Y1[-1] + np.array([1, 2]) * np.diff(Y1[-2:])])

    if plot_density:
        ax.pcolor(X, Y, H.max() - H.T, cmap=density_cmap)

    if plot_contours:
        if contour_kwargs is None:
            contour_kwargs = dict()
            alphas = np.linspace(0.2, 1, len(levels))[::-1]
            for ii, V in enumerate(Vs):
#                contour_kwargs['alpha'] = contour_kwargs.get('alpha', alphas[ii])
#                ax.contour(X2, Y2, H2.T, V, colors = color, contour_kwargs=contour_kwargs, **kwargs) # alpha = alphas[ii], 
                ax.contour(X2, Y2, H2.T, V, colors = color, alpha = alphas[ii])

        else:
            for ii, V in enumerate(Vs):
                ax.contour(X2, Y2, H2.T, V, colors = color, **contour_kwargs, **kwargs)

    ax.set_xlim(span[0])
    ax.set_ylim(span[1])

    return ax


def traceplot_custom(results_list, quantiles=[0.025, 0.5, 0.975],
                     smooth=0.02, thin=1, dims=None,
                     contour_labels_list=None,
                     post_color_list=['blue'], post_kwargs=None, kde=True, nkde=1000,
                     trace_cmap='plasma', trace_color=None, trace_kwargs=None,
                     connect=False, connect_highlight=10, connect_color='red',
                     connect_kwargs=None, max_n_ticks=5, use_math_text=False,
                     labels=None, label_kwargs=None,
                     show_titles=False, title_fmt=".2f", title_kwargs=None,
                     truths=None, truth_color='red', truth_kwargs=None,
                     verbose=False, fig=None):
    """
    Plot traces and marginalized posteriors for each parameter.
    Allows you to plot multiple trace plots on top of each other.
    The keywords are mostly the same as the dynesty default, only listing the new keywords here.

    Parameters
    ----------
    results_list : list of :class:`~dynesty.results.Results` instance
        A :class:`~dynesty.results.Results` instance from a nested
        sampling run. **Compatible with results derived from**
        `nestle <http://kylebarbary.com/nestle/>`_.

    color_list : list of length the same as results_list
        List of `~matplotlib`-style colors.
    
    contour_labels_list : list of length the same as results_list
        List of strings for labelling each contour.

    Returns
    -------
    traceplot : (`~matplotlib.figure.Figure`, `~matplotlib.axes.Axes`)
        Output trace plot.

    """

    # Initialize values.
    if title_kwargs is None:
        title_kwargs = dict()
    if label_kwargs is None:
        label_kwargs = dict()
    if trace_kwargs is None:
        trace_kwargs = dict()
    if connect_kwargs is None:
        connect_kwargs = dict()
    if post_kwargs is None:
        post_kwargs = dict()
    if truth_kwargs is None:
        truth_kwargs = dict()

    # Set defaults.
    connect_kwargs['alpha'] = connect_kwargs.get('alpha', 0.7)
    post_kwargs['alpha'] = post_kwargs.get('alpha', 0.6)
    trace_kwargs['s'] = trace_kwargs.get('s', 3)
    trace_kwargs['edgecolor'] = trace_kwargs.get('edgecolor', None)
    trace_kwargs['edgecolors'] = trace_kwargs.get('edgecolors', None)
    truth_kwargs['linestyle'] = truth_kwargs.get('linestyle', 'solid')
    truth_kwargs['linewidth'] = truth_kwargs.get('linewidth', 2)

    samples_list = []
    weights_list = []
    span_list_lo = []
    span_list_hi = []

    for results in results_list:
        # Extract weighted samples.
        samples = results['samples']
        logvol = results['logvol']
        try:
            weights = np.exp(results['logwt'] - results['logz'][-1])
        except:
            weights = results['weights']
        if kde:
            # Derive kernel density estimate.
            wt_kde = gaussian_kde(resample_equal(-logvol, weights))  # KDE
            logvol_grid = np.linspace(logvol[0], logvol[-1], nkde)  # resample
            wt_grid = wt_kde.pdf(-logvol_grid)  # evaluate KDE PDF
            wts = np.interp(-logvol, -logvol_grid, wt_grid)  # interpolate
        else:
            wts = weights

        # Deal with 1D results. A number of extra catches are also here
        # in case users are trying to plot other results besides the `Results`
        # instance generated by `dynesty`.
        samples = np.atleast_1d(samples)
        if len(samples.shape) == 1:
            samples = np.atleast_2d(samples)
        else:
            assert len(samples.shape) == 2, "Samples must be 1- or 2-D."
            samples = samples.T
        assert samples.shape[0] <= samples.shape[1], "There are more " \
                                                 "dimensions than samples!"

        # Slice samples based on provided `dims`.
        if dims is not None:
            samples = samples[dims]
        ndim, nsamps = samples.shape

        # Check weights.
        if weights.ndim != 1:
            raise ValueError("Weights must be 1-D.")
        if nsamps != weights.shape[0]:
            raise ValueError("The number of weights and samples disagree!")

        # Check ln(volume).
        if logvol.ndim != 1:
            raise ValueError("Ln(volume)'s must be 1-D.")
        if nsamps != logvol.shape[0]:
            raise ValueError("The number of ln(volume)'s and samples disagree!")

        # Check sample IDs.
        if connect:
            try:
                samples_id = results['samples_id']
                uid = np.unique(samples_id)
            except:
                raise ValueError("Sample IDs are not defined!")
            try:
                ids = connect_highlight[0]
                ids = connect_highlight
            except:
                ids = np.random.choice(uid, size=connect_highlight, replace=False)

        # Determine plotting bounds for marginalized 1-D posteriors.
        span = [0.999999426697 for i in range(ndim)]
        span = list(span)
        span_lo = list(span)
        span_hi = list(span)
        if len(span) != ndim:
            raise ValueError("Dimension mismatch between samples and span.")
        for i, _ in enumerate(span):
            try:
                xmin, xmax = span[i]
            except:
                q = [0.5 - 0.5 * span[i], 0.5 + 0.5 * span[i]]
                span_lo[i] = _quantile(samples[i], q, weights=weights)[0]
                span_hi[i] = _quantile(samples[i], q, weights=weights)[1]

        samples_list.append(samples)
        weights_list.append(weights)
        span_list_hi.append(span_hi)
        span_list_lo.append(span_lo)

    span = []
    for param in np.arange(len(span_list_hi[0])):
        list_hi = []
        list_lo = []
        for nres in np.arange(len(span_list_hi)):
            list_hi.append(span_list_hi[nres][param])
            list_lo.append(span_list_lo[nres][param])
        hi = np.max(list_hi)
        lo = np.min(list_lo)
        span.append([lo, hi])

    # Setting up labels.
    if labels is None:
        labels = [r"$x_{"+str(i+1)+"}$" for i in range(ndim)]

    # Setting up smoothing.
    if (isinstance(smooth, int_type) or isinstance(smooth, float_type)):
        smooth = [smooth for i in range(ndim)]

    # Setting up default plot layout.
    if fig is None:
        fig, axes = pl.subplots(ndim, 2, figsize=(12, 3*ndim))
    else:
        fig, axes = fig
        try:
            axes.reshape(ndim, 2)
        except:
            raise ValueError("Provided axes do not match the required shape "
                             "for plotting samples.")

    # Plotting.
    for j, samples in enumerate(samples_list):
        weights = weights_list[j]
        color = color_list[j]
        if contour_labels_list is not None:
            contour_label = contour_labels_list[j]
        for i, x in enumerate(samples):
    
            # Plot trace.
    
            # Establish axes.
            if np.shape(samples)[0] == 1:
                ax = axes[1]
            else:
                ax = axes[i, 0]
            # Set color(s)/colormap(s).
            if trace_color is not None:
                if isinstance(trace_color, str_type):
                    color = trace_color
                else:
                    color = trace_color[i]
            else:
                color = wts[::thin]
            if isinstance(trace_cmap, str_type):
                cmap = trace_cmap
            else:
                cmap = trace_cmap[i]
            # Setup axes.
            ax.set_xlim([0., -min(logvol)])
            ax.set_ylim([min(x), max(x)])
            if max_n_ticks == 0:
                ax.xaxis.set_major_locator(NullLocator())
                ax.yaxis.set_major_locator(NullLocator())
            else:
                ax.xaxis.set_major_locator(MaxNLocator(max_n_ticks))
                ax.yaxis.set_major_locator(MaxNLocator(max_n_ticks))
            # Label axes.
            sf = ScalarFormatter(useMathText=use_math_text)
            ax.yaxis.set_major_formatter(sf)
            ax.set_xlabel(r"$-\ln X$", **label_kwargs)
            ax.set_ylabel(labels[i], **label_kwargs)
            # Generate scatter plot.
            ax.scatter(-logvol[::thin], x[::thin], c=color, cmap=cmap,
                       **trace_kwargs)
            if connect:
                # Add lines highlighting specific particle paths.
                for j in ids:
                    sel = (samples_id[::thin] == j)
                    ax.plot(-logvol[::thin][sel], x[::thin][sel],
                            color=connect_color, **connect_kwargs)
            # Add truth value(s).
            if truths is not None and truths[i] is not None:
                try:
                    [ax.axhline(t, color=truth_color, **truth_kwargs)
                     for t in truths[i]]
                except:
                    ax.axhline(truths[i], color=truth_color, **truth_kwargs)
    
            # Plot marginalized 1-D posterior.
    
            # Establish axes.
            if np.shape(samples)[0] == 1:
                ax = axes[0]
            else:
                ax = axes[i, 1]
            # Set color(s).
            if isinstance(post_color, str_type):
                color = post_color
            else:
                color = post_color[i]
            # Setup axes
            ax.set_xlim(span[i])
            if max_n_ticks == 0:
                ax.xaxis.set_major_locator(NullLocator())
                ax.yaxis.set_major_locator(NullLocator())
            else:
                ax.xaxis.set_major_locator(MaxNLocator(max_n_ticks))
                ax.yaxis.set_major_locator(NullLocator())
            # Label axes.
            sf = ScalarFormatter(useMathText=use_math_text)
            ax.xaxis.set_major_formatter(sf)
            ax.set_xlabel(labels[i], **label_kwargs)
            # Generate distribution.
            s = smooth[i]
            if isinstance(s, int_type):
                # If `s` is an integer, plot a weighted histogram with
                # `s` bins within the provided bounds.
                n, b, _ = ax.hist(x, bins=s, weights=weights, color=color,
                                  range=np.sort(span[i]), **post_kwargs)
                x0 = np.array(list(zip(b[:-1], b[1:]))).flatten()
                y0 = np.array(list(zip(n, n))).flatten()
            else:
                # If `s` is a float, oversample the data relative to the
                # smoothing filter by a factor of 10, then use a Gaussian
                # filter to smooth the results.
                bins = int(round(10. / s))
                n, b = np.histogram(x, bins=bins, weights=weights,
                                    range=np.sort(span[i]))
                n = norm_kde(n, 10.)
                x0 = 0.5 * (b[1:] + b[:-1])
                y0 = n
                ax.fill_between(x0, y0, color=color, **post_kwargs)
            ax.set_ylim([0., max(y0) * 1.05])
            # Plot quantiles.
            if quantiles is not None and len(quantiles) > 0:
                qs = _quantile(x, quantiles, weights=weights)
                for q in qs:
                    ax.axvline(q, lw=2, ls="dashed", color=color)
                if verbose:
                    print("Quantiles:")
                    print(labels[i], [blob for blob in zip(quantiles, qs)])
            # Add truth value(s).
            if truths is not None and truths[i] is not None:
                try:
                    [ax.axvline(t, color=truth_color, **truth_kwargs)
                     for t in truths[i]]
                except:
                    ax.axvline(truths[i], color=truth_color, **truth_kwargs)
            # Set titles.
            if show_titles:
                title = None
                if title_fmt is not None:
                    ql, qm, qh = _quantile(x, [0.025, 0.5, 0.975], weights=weights)
                    q_minus, q_plus = qm - ql, qh - qm
                    fmt = "{{0:{0}}}".format(title_fmt).format
                    title = r"${{{0}}}_{{-{1}}}^{{+{2}}}$"
                    title = title.format(fmt(qm), fmt(q_minus), fmt(q_plus))
                    title = "{0} = {1}".format(labels[i], title)
                    ax.set_title(title, **title_kwargs)
    
    return fig, axes


def cornerplot_custom(results_list, dims=None, quantiles=[0.025, 0.5, 0.975],
               color_list=['blue'], smooth=0.02, quantiles_2d=None, hist_kwargs=None,
               hist2d_kwargs=None, labels=None, label_kwargs=None,
               contour_labels_list=None,
               show_titles=False, title_fmt=".2f", title_kwargs=None,
               truths=None, truth_color='red', truth_kwargs=None,
               max_n_ticks=5, top_ticks=False, use_math_text=False,
               verbose=False, fig=None):
    """
    Generate a corner plot of the 1-D and 2-D marginalized posteriors.
    Allows you to plot multiple corner plots on top of each other.
    The keywords are mostly the same as dynesty default, only listing the new keywords here.

    Parameters
    ----------
    results_list : list of  :class:`~dynesty.results.Results` instance
        A :class:`~dynesty.results.Results` instance from a nested
        sampling run. **Compatible with results derived from**
        `nestle <http://kylebarbary.com/nestle/>`_.

    color_list : list of length the same as results_list
        List of `~matplotlib`-style colors.
    
    contour_labels_list : list of length the same as results_list
        List of strings for labelling each contour.

    Returns
    -------
    cornerplot : (`~matplotlib.figure.Figure`, `~matplotlib.axes.Axes`)
        Output corner plot.

    """

    # Initialize values.
    if quantiles is None:
        quantiles = []
    if truth_kwargs is None:
        truth_kwargs = dict()
    if label_kwargs is None:
        label_kwargs = dict()
    if title_kwargs is None:
        title_kwargs = dict()
    if hist_kwargs is None:
        hist_kwargs = dict()
    if hist2d_kwargs is None:
        hist2d_kwargs = dict()

    # Set defaults.
    hist_kwargs['alpha'] = hist_kwargs.get('alpha', 0.6)
    hist2d_kwargs['alpha'] = hist2d_kwargs.get('alpha', 0.6)
    hist2d_kwargs['levels'] = hist2d_kwargs.get('levels', quantiles_2d)
    truth_kwargs['linestyle'] = truth_kwargs.get('linestyle', 'solid')
    truth_kwargs['linewidth'] = truth_kwargs.get('linewidth', 2)
    truth_kwargs['alpha'] = truth_kwargs.get('alpha', 0.7)

    samples_list = []
    weights_list = []
    span_list_lo = []
    span_list_hi = []

    for results in results_list:
        # Extract weighted samples.
        samples = results['samples']
        try:
            weights = np.exp(results['logwt'] - results['logz'][-1])
        except:
            weights = results['weights']
    
        # Deal with 1D results. A number of extra catches are also here
        # in case users are trying to plot other results besides the `Results`
        # instance generated by `dynesty`.
        samples = np.atleast_1d(samples)
        if len(samples.shape) == 1:
            samples = np.atleast_2d(samples)
        else:
            assert len(samples.shape) == 2, "Samples must be 1- or 2-D."
            samples = samples.T
        assert samples.shape[0] <= samples.shape[1], "There are more " \
                                                     "dimensions than samples!"
    
        # Slice samples based on provided `dims`.
        if dims is not None:
            samples = samples[dims]
        ndim, nsamps = samples.shape
    
        # Check weights.
        if weights.ndim != 1:
            raise ValueError("Weights must be 1-D.")
        if nsamps != weights.shape[0]:
            raise ValueError("The number of weights and samples disagree!")
    
        # Determine plotting bounds.
        span = [0.999999426697 for i in range(ndim)]
        span = list(span)
        span_lo = list(span)
        span_hi = list(span)
        if len(span) != ndim:
            raise ValueError("Dimension mismatch between samples and span.")
        for i, _ in enumerate(span):
            try:
                xmin, xmax = span[i]
            except:
                q = [0.5 - 0.5 * span[i], 0.5 + 0.5 * span[i]]
                span_lo[i] = _quantile(samples[i], q, weights=weights)[0]
                span_hi[i] = _quantile(samples[i], q, weights=weights)[1]
        
        samples_list.append(samples)
        weights_list.append(weights)
        span_list_hi.append(span_hi)
        span_list_lo.append(span_lo)

    span = []
    for param in np.arange(len(span_list_hi[0])):
        list_hi = []
        list_lo = []
        for nres in np.arange(len(span_list_hi)):
            list_hi.append(span_list_hi[nres][param])
            list_lo.append(span_list_lo[nres][param])
        hi = np.max(list_hi)
        lo = np.min(list_lo)
        span.append([lo, hi])
        
    # Set labels
    if labels is None:
        labels = [r"$x_{"+str(i+1)+"}$" for i in range(ndim)]
    
    # Setting up smoothing.
    if (isinstance(smooth, int_type) or isinstance(smooth, float_type)):
        smooth = [smooth for i in range(ndim)]
    
    # Setup axis layout (from `corner.py`).
    factor = 2.0  # size of side of one panel
    lbdim = 0.5 * factor  # size of left/bottom margin
    trdim = 0.2 * factor  # size of top/right margin
    whspace = 0.05  # size of width/height margin
    plotdim = factor * ndim + factor * (ndim - 1.) * whspace  # plot size
    dim = lbdim + plotdim + trdim  # total size

    # Initialize figure.
    if fig is None:
        fig, axes = plt.subplots(ndim, ndim, figsize=(dim, dim))
    else:
        try:
            fig, axes = fig
            axes = np.array(axes).reshape((ndim, ndim))
        except:
            raise ValueError("Mismatch between axes and dimension.")

    # Format figure.
    lb = lbdim / dim
    tr = (lbdim + plotdim) / dim
    fig.subplots_adjust(left=lb, bottom=lb, right=tr, top=tr,
                        wspace=whspace, hspace=whspace)

    # Plotting.
    for j, samples in enumerate(samples_list):
        weights = weights_list[j]
        color = color_list[j]
        if contour_labels_list is not None:
            contour_label = contour_labels_list[j]
        for i, x in enumerate(samples):
            if np.shape(samples)[0] == 1:
                ax = axes
            else:
                ax = axes[i, i]
    
            # Plot the 1-D marginalized posteriors.
    
            # Setup axes
            ax.set_xlim(span[i])
            if max_n_ticks == 0:
                ax.xaxis.set_major_locator(NullLocator())
                ax.yaxis.set_major_locator(NullLocator())
            else:
                ax.xaxis.set_major_locator(MaxNLocator(max_n_ticks,
                                                       prune="lower"))
                ax.yaxis.set_major_locator(NullLocator())
            # Label axes.
            sf = ScalarFormatter(useMathText=use_math_text)
            ax.xaxis.set_major_formatter(sf)
            if i < ndim - 1:
                if top_ticks:
                    ax.xaxis.set_ticks_position("top")
                    [l.set_rotation(45) for l in ax.get_xticklabels()]
                else:
                    ax.set_xticklabels([])
            else:
                [l.set_rotation(45) for l in ax.get_xticklabels()]
                ax.set_xlabel(labels[i], **label_kwargs)
                ax.xaxis.set_label_coords(0.5, -0.3)
            # Generate distribution.
            sx = smooth[i]
            if isinstance(sx, int_type):
                # If `sx` is an integer, plot a weighted histogram with
                # `sx` bins within the provided bounds.
                n, b, _ = ax.hist(x, bins=sx, weights=weights, color=color,
                                  range=np.sort(span[i]), **hist_kwargs)
            else:
                # If `sx` is a float, oversample the data relative to the
                # smoothing filter by a factor of 10, then use a Gaussian
                # filter to smooth the results.
                bins = int(round(10. / sx))
                n, b = np.histogram(x, bins=bins, weights=weights,
                                    range=np.sort(span[i]))
                n = norm_kde(n, 10.)
                b0 = 0.5 * (b[1:] + b[:-1])
                n, b, _ = ax.hist(b0, bins=b, weights=n,
                                  range=np.sort(span[i]), color=color,
                                  **hist_kwargs)
            ax.set_ylim([0., max(n) * 1.05])
            # Plot quantiles.
            if quantiles is not None and len(quantiles) > 0:
                qs = _quantile(x, quantiles, weights=weights)
                for q in qs:
                    ax.axvline(q, lw=2, ls="dashed", color=color)
                if verbose:
                    print("Quantiles:")
                    print(labels[i], [blob for blob in zip(quantiles, qs)])
            # Add truth value(s).
            if truths is not None and truths[i] is not None:
                try:
                    [ax.axvline(t, color=truth_color, **truth_kwargs)
                     for t in truths[i]]
                except:
                    ax.axvline(truths[i], color=truth_color, **truth_kwargs)
            # Set titles.
            if show_titles:
                title = None
                if title_fmt is not None:
                    ql, qm, qh = _quantile(x, [0.025, 0.5, 0.975], weights=weights)
                    q_minus, q_plus = qm - ql, qh - qm
                    fmt = "{{0:{0}}}".format(title_fmt).format
                    title = r"${{{0}}}_{{-{1}}}^{{+{2}}}$"
                    title = title.format(fmt(qm), fmt(q_minus), fmt(q_plus))
                    title = "{0} = {1}".format(labels[i], title)
                    ax.set_title(title, **title_kwargs)
    
            for j, y in enumerate(samples):
                if np.shape(samples)[0] == 1:
                    ax = axes
                else:
                    ax = axes[i, j]
    
                # Plot the 2-D marginalized posteriors.
    
                # Setup axes.
                if j > i:
                    ax.set_frame_on(False)
                    ax.set_xticks([])
                    ax.set_yticks([])
                    continue
                elif j == i:
                    continue
    
                if max_n_ticks == 0:
                    ax.xaxis.set_major_locator(NullLocator())
                    ax.yaxis.set_major_locator(NullLocator())
                else:
                    ax.xaxis.set_major_locator(MaxNLocator(max_n_ticks,
                                                           prune="lower"))
                    ax.yaxis.set_major_locator(MaxNLocator(max_n_ticks,
                                                           prune="lower"))
                # Label axes.
                sf = ScalarFormatter(useMathText=use_math_text)
                ax.xaxis.set_major_formatter(sf)
                ax.yaxis.set_major_formatter(sf)
                if i < ndim - 1:
                    ax.set_xticklabels([])
                else:
                    [l.set_rotation(45) for l in ax.get_xticklabels()]
                    ax.set_xlabel(labels[j], **label_kwargs)
                    ax.xaxis.set_label_coords(0.5, -0.3)
                if j > 0:
                    ax.set_yticklabels([])
                else:
                    [l.set_rotation(45) for l in ax.get_yticklabels()]
                    ax.set_ylabel(labels[i], **label_kwargs)
                    ax.yaxis.set_label_coords(-0.3, 0.5)
                # Generate distribution.
                sy = smooth[j]
                check_ix = isinstance(sx, int_type)
                check_iy = isinstance(sy, int_type)
                if check_ix and check_iy:
                    fill_contours = False
                    plot_contours = False
                else:
                    fill_contours = True
                    plot_contours = True
                hist2d_kwargs['fill_contours'] = hist2d_kwargs.get('fill_contours',
                                                                   fill_contours)
                hist2d_kwargs['plot_contours'] = hist2d_kwargs.get('plot_contours',
                                                                   plot_contours)
                hist_kwargs['alpha'] = hist_kwargs.get('alpha', 0.6)
#                hist2d_kwargs['label'] = hist_kwargs.get('label', contour_label)

                dyplot._hist2d(y, x, ax=ax, span=[span[j], span[i]],
                               weights=weights, color=color, smooth=[sy, sx],
                               **hist2d_kwargs)
                # Add truth values
                if truths is not None:
                    if truths[j] is not None:
                        try:
                            [ax.axvline(t, color=truth_color, **truth_kwargs)
                             for t in truths[j]]
                        except:
                            ax.axvline(truths[j], color=truth_color,
                                       **truth_kwargs)
                    if truths[i] is not None:
                        try:
                            [ax.axhline(t, color=truth_color, **truth_kwargs)
                             for t in truths[i]]
                        except:
                            ax.axhline(truths[i], color=truth_color,
                                       **truth_kwargs)
    return (fig, axes)
