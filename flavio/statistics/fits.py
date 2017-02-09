"""A fit is a collection of observables and parameters that can be used to
perform statistical analyses within a particular statistical framework.

Fits are instances of descendants of the `Fit` class (which is not meant
to be used directly)."""

import flavio
import numpy as np
import copy
from flavio.statistics.probability import NormalDistribution, MultivariateNormalDistribution, convolve_distributions

class Fit(flavio.NamedInstanceClass):
    """Base class for fits. Not meant to be used directly."""

    def __init__(self,
                 name,
                 par_obj,
                 fit_parameters,
                 nuisance_parameters,
                 observables,
                 fit_wc_names=[],
                 fit_wc_function=None,
                 fit_wc_priors=None,
                 input_scale=160.,
                 exclude_measurements=None,
                 include_measurements=None,
                ):
        # some checks to make sure the input is sane
        for p in fit_parameters + nuisance_parameters:
            # check that fit and nuisance parameters exist
            assert p in par_obj._parameters.keys(), "Parameter " + p + " not found in Constraints"
        for obs in observables:
            # check that observables exist
            try:
                if isinstance(obs, tuple):
                    flavio.classes.Observable.get_instance(obs[0])
                else:
                    flavio.classes.Observable.get_instance(obs)
            except:
                raise ValueError("Observable " + str(obs) + " not found!")
        _obs_measured = set()
        # check that observables are constrained
        for m_name, m_obj in flavio.classes.Measurement.instances.items():
            _obs_measured.update(m_obj.all_parameters)
        missing_obs = set(observables) - set(_obs_measured).intersection(set(observables))
        assert missing_obs == set(), "No measurement found for the observables: " + ', '.join(missing_obs)
        if exclude_measurements is not None and include_measurements is not None:
            raise ValueError("The options exclude_measurements and include_measurements must not be specified simultaneously")
        # check that no parameter appears as fit *and* nuisance parameter
        intersect = set(fit_parameters).intersection(nuisance_parameters)
        assert intersect == set(), "Parameters appearing as fit_parameters and nuisance_parameters: " + str(intersect)
        # check that the Wilson coefficient function works
        if fit_wc_names: # if list of WC names not empty
            try:
                fit_wc_function(**{fit_wc_name: 1e-6 for fit_wc_name in fit_wc_names})
            except:
                raise ValueError("Error in calling the Wilson coefficient function")
        # now that everything seems fine, we can call the init of the parent class
        super().__init__(name)
        self.par_obj = par_obj
        self.parameters_central = self.par_obj.get_central_all()
        self.fit_parameters = fit_parameters
        self.nuisance_parameters = nuisance_parameters
        self.exclude_measurements = exclude_measurements
        self.include_measurements = include_measurements
        self.fit_wc_names = fit_wc_names
        self.fit_wc_function = fit_wc_function
        self.fit_wc_priors = fit_wc_priors
        self.observables = observables
        self.input_scale = input_scale

    @property
    def get_central_fit_parameters(self):
        """Return a numpy array with the central values of all fit parameters."""
        return np.asarray([self.parameters_central[p] for p in self.fit_parameters])

    @property
    def get_random_fit_parameters(self):
        """Return a numpy array with random values for all fit parameters."""
        all_random = self.par_obj.get_random_all()
        return np.asarray([all_random[p] for p in self.fit_parameters])

    @property
    def get_random_wilson_coeffs(self):
        """Return a numpy array with random values for all Wilson coefficients."""
        if self.fit_wc_priors is None:
            return None
        all_random = self.fit_wc_priors.get_random_all()
        return np.asarray([all_random[p] for p in self.fit_wc_names])

    @property
    def get_central_nuisance_parameters(self):
        """Return a numpy array with the central values of all nuisance parameters."""
        return np.asarray([self.parameters_central[p] for p in self.nuisance_parameters])

    @property
    def get_random_nuisance_parameters(self):
        """Return a numpy array with random values for all nuisance parameters."""
        all_random = self.par_obj.get_random_all()
        return np.asarray([all_random[p] for p in self.nuisance_parameters])

    @property
    def get_measurements(self):
        """Return a list of all the measurements currently defined that
        constrain any of the fit observables."""
        all_measurements = []
        for m_name, m_obj in flavio.classes.Measurement.instances.items():
            if set(m_obj.all_parameters).isdisjoint(self.observables):
                # if set of all observables constrained by measurement is disjoint
                # with fit observables, do nothing
                continue
            else:
                # else, add measurement name to output list
                all_measurements.append(m_name)
        if self.exclude_measurements is None and self.include_measurements is None:
            return all_measurements
        elif self.exclude_measurements is not None:
            return list(set(all_measurements) - set(self.exclude_measurements))
        elif self.include_measurements is not None:
            return list(set(all_measurements) & set(self.include_measurements))




class BayesianFit(Fit):
    """Bayesian fit class. Instances of this class can then be fed to samplers.

    Parameters
    ----------

    - `name`: a descriptive string name
    - `par_obj`: an instance of `ParameterConstraints`, e.g. `flavio.default_parameters`
    - `fit_parameters`: a list of string names of parameters of interest. The existing
      constraints on the parameter will be taken as prior.
    - `nuisance_parameters`: a list of string names of nuisance parameters. The existing
      constraints on the parameter will be taken as prior.
    - `observables`: a list of observable names to be included in the fit
    - `exclude_measurements`: optional; a list of measurement names *not* to be included in
    the fit. By default, all existing measurements are included.
    - `include_measurements`: optional; a list of measurement names to be included in
    the fit. By default, all existing measurements are included.
    - `fit_wc_names`: optional; a list of string names of arguments of the Wilson
      coefficient function below
    - `fit_wc_function`: optional; a function that has exactly the arguements listed
      in `fit_wc_names` and returns a dictionary that can be fed to the `set_initial`
      method of the Wilson coefficient class. Example: fit the real and imaginary
      parts of $C_{10}$ in $b\to s\mu^+\mu^-$.
    ```
    def fit_wc_function(Re_C10, Im_C10):
        return {'C10_bsmmumu': Re_C10 + 1j*Im_C10}
    ```
    - `input_scale`: input scale for the Wilson coeffficients. Defaults to 160.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.dimension = len(self.fit_parameters) + len(self.nuisance_parameters) + len(self.fit_wc_names)

    def array_to_dict(self, x):
        """Convert a 1D numpy array of floats to a dictionary of fit parameters,
        nuisance parameters, and Wilson coefficients."""
        d = {}
        n_fit_p = len(self.fit_parameters)
        n_nui_p = len(self.nuisance_parameters)
        n_wc = len(self.fit_wc_names)
        d['fit_parameters'] = { p: x[i] for i, p in enumerate(self.fit_parameters) }
        d['nuisance_parameters'] = { p: x[i + n_fit_p] for i, p in enumerate(self.nuisance_parameters) }
        d['fit_wc'] = { p: x[i + n_fit_p + n_nui_p] for i, p in enumerate(self.fit_wc_names) }
        return d

    def dict_to_array(self, d):
        """Convert a dictionary of fit parameters,
        nuisance parameters, and Wilson coefficients to a 1D numpy array of
        floats."""
        n_fit_p = len(self.fit_parameters)
        n_nui_p = len(self.nuisance_parameters)
        n_wc = len(self.fit_wc_names)
        arr = np.zeros(n_fit_p + n_nui_p + n_wc)
        arr[:n_fit_p] = [d['fit_parameters'][p] for p in self.fit_parameters]
        arr[n_fit_p:n_fit_p+n_nui_p] = [d['nuisance_parameters'][p] for p in self.nuisance_parameters]
        arr[n_fit_p+n_nui_p:]   = [d['fit_wc'][c] for c in self.fit_wc_names]
        return arr

    @property
    def get_random(self):
        """Get an array with random values for all the fit and nuisance
        parameters"""
        arr = np.zeros(self.dimension)
        n_fit_p = len(self.fit_parameters)
        n_nui_p = len(self.nuisance_parameters)
        arr[:n_fit_p] = self.get_random_fit_parameters
        arr[n_fit_p:n_fit_p+n_nui_p] = self.get_random_nuisance_parameters
        arr[n_fit_p+n_nui_p:] = self.get_random_wilson_coeffs
        return arr

    def get_par_dict(self, x):
        """Get a dictionary of fit and nuisance parameters from an input array"""
        d = self.array_to_dict(x)
        par_dict = self.parameters_central.copy()
        par_dict.update(d['fit_parameters'])
        par_dict.update(d['nuisance_parameters'])
        return par_dict

    def get_wc_obj(self, x):
        wc_obj = flavio.WilsonCoefficients()
        # if there are no WCs to be fitted, return the SM WCs
        if not self.fit_wc_names:
            return wc_obj
        d = self.array_to_dict(x)
        wc_obj.set_initial(self.fit_wc_function(**d['fit_wc']), self.input_scale)
        return wc_obj

    def log_prior_parameters(self, x):
        """Return the prior probability for all fit and nuisance parameters
        given an input array"""
        par_dict = self.get_par_dict(x)
        exclude_parameters = list(set(par_dict.keys())-set(self.fit_parameters)-set(self.nuisance_parameters))
        prob_dict = self.par_obj.get_logprobability_all(par_dict, exclude_parameters=exclude_parameters)
        return sum([p for obj, p in prob_dict.items()])

    def log_prior_wilson_coeffs(self, x):
        """Return the prior probability for all Wilson coefficients
        given an input array"""
        if self.fit_wc_priors is None:
            return 0
        wc_dict = self.array_to_dict(x)['fit_wc']
        prob_dict = self.fit_wc_priors.get_logprobability_all(wc_dict)
        return sum([p for obj, p in prob_dict.items()])

    def get_predictions(self, x):
        """Get a dictionary with predictions for all observables given an input
        array"""
        par_dict = self.get_par_dict(x)
        wc_obj = self.get_wc_obj(x)
        all_predictions = {}
        for observable in self.observables:
            if isinstance(observable, tuple):
                obs_name = observable[0]
                _inst = flavio.classes.Observable.get_instance(obs_name)
                all_predictions[observable] = _inst.prediction_par(par_dict, wc_obj, *observable[1:])
            else:
                _inst = flavio.classes.Observable.get_instance(observable)
                all_predictions[observable] = _inst.prediction_par(par_dict, wc_obj)
        return all_predictions

    def log_likelihood(self, x):
        """Return the logarithm of the likelihood function (not including the
        prior)"""
        predictions = self.get_predictions(x)
        ll = 0.
        for measurement in self.get_measurements:
            m_obj = flavio.Measurement.get_instance(measurement)
            m_obs = m_obj.all_parameters
            exclude_observables = set(m_obs) - set(self.observables)
            prob_dict = m_obj.get_logprobability_all(predictions, exclude_parameters=exclude_observables)
            ll += sum(prob_dict.values())
        return ll

    def log_target(self, x):
        """Return the logarithm of the likelihood times prior probability"""
        return self.log_likelihood(x) + self.log_prior_parameters(x) + self.log_prior_wilson_coeffs(x)


class FastFit(BayesianFit):
    """A subclass of `BayesianFit` that is meant to produce fast likelihood
    contour plots.

    Calling the method `make_measurement`, a pseudo-measurement is generated
    that combines the actual experimental measurements with the theoretical
    uncertainties stemming from the nuisance parameters. This is done by
    generating random samples of the nuisance parameters and evaluating all
    observables within the Standard Model many times (100 by default).
    Then, the covariance of all predictions is extracted. The probability
    distributions of the experimental measurements are then convolved with
    the appropriate (and correlated) theoretical uncertainties.

    This approach has the advantage that two-dimensional plots of the likelihood
    can be produced without the need for sampling or profiling the other
    dimensions. However, several strong assumptions go into this method, most
    importantly,

    - theoretical uncertainties are treated as Gaussian
      (experimental uncertainties are instead treated exactly!)
    - the theoretical uncertainties in the presence of new physics are assumed
      to be similar to the ones in the SM
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.measurements = None


    # a method to get the mean and covariance of all measurements of all
    # observables of interest
    def _get_central_covariance_experiment(self, N=5000):
        means = []
        covariances = []
        for measurement in self.get_measurements:
            m_obj = flavio.Measurement.get_instance(measurement)
            # obs. included in the fit and constrained by this measurement
            our_obs = set(m_obj.all_parameters).intersection(self.observables)
            # construct a dict. containing a vector of N random values for
            # each of these observables
            random_dict = {}
            for obs in our_obs:
                random_dict[obs] = np.zeros(N)
            for i in range(N):
                m_random = m_obj.get_random_all()
                for obs in our_obs:
                    random_dict[obs][i] = m_random[obs]
            # mean = np.zeros(len(self.observables))
            random_arr = np.zeros((len(self.observables), N))
            for i, obs in enumerate(self.observables):
                #     n = len(random_dict[obs])
                if obs in our_obs:
                    random_arr[i] = random_dict[obs]
            mean = np.mean(random_arr, axis=1)
            covariance = np.cov(random_arr)
            for i, obs in enumerate(self.observables):
                if obs not in our_obs:
                    covariance[:,i] = 0
                    covariance[i, :] = 0
                    covariance[i, i] = np.inf
            means.append(mean)
            covariances.append(covariance)
        # if there is only a single measuement
        if len(means) == 1:
            return means[0], covariances[0]
        # if there are severeal measurements, perform a weighted average
        else:
            # covariances: [Sigma_1, Sigma_2, ...]
            # means: [x_1, x_2, ...]
            # weights_ [W_1, W_2, ...] where W_i = (Sigma_i)^(-1)
            # weighted covariance is  (W_1 + W_2 + ...)^(-1) = Sigma
            # weigted mean is  Sigma.(W_1.x_1 + W_2.x_2 + ...) = x
            if len(self.observables) == 1:
                weights = np.array([1/c for c in covariances])
                weighted_covariance = 1/np.sum(weights, axis=0)
                weighted_mean = weighted_covariance * np.sum(
                                [np.dot(weights[i], means[i]) for i in range(len(means))])
            else:
                weights = [np.linalg.inv(c) for c in covariances]
                weighted_covariance = np.linalg.inv(np.sum(weights, axis=0))
                weighted_mean = np.dot(weighted_covariance, np.sum(
                                [np.dot(weights[i], means[i]) for i in range(len(means))],
                                axis=0))
            return weighted_mean, weighted_covariance


    # a method to get the covariance of the SM prediction of all observables
    # of interest
    def _get_covariance_sm(self, N=100):
        par_central = self.par_obj.get_central_all()
        def random_nuisance_dict():
            arr = self.get_random_nuisance_parameters
            nuis_dict = {par: arr[i] for i, par in enumerate(self.nuisance_parameters)}
            par = par_central.copy()
            par.update(nuis_dict)
            return par
        par_random = [random_nuisance_dict() for i in range(N)]

        pred_arr = np.zeros((len(self.observables), N))
        wc_sm = flavio.WilsonCoefficients()
        for i, observable in enumerate(self.observables):
            if isinstance(observable, tuple):
                obs_name = observable[0]
                _inst = flavio.classes.Observable.get_instance(obs_name)
                pred_arr[i] = np.array([_inst.prediction_par(par, wc_sm, *observable[1:])
                                        for par in par_random])
            else:
                _inst = flavio.classes.Observable.get_instance(observable)
                pred_arr[i] = np.array([_inst.prediction_par(par, wc_sm)
                                        for par in par_random])
        return np.cov(pred_arr)

    def make_measurement(self, N=100, Nexp=5000):
        """Initialize the fit by producing a pseudo-measurement containing both
        experimental uncertainties as well as theory uncertainties stemming
        from nuisance parameters."""
        cov_sm = self._get_covariance_sm(N)
        # add the Pseudo-measurements
        for measurement in self.get_measurements:
            m_obj = flavio.classes.Measurement.get_instance(measurement)
            pm = flavio.classes.PseudoMeasurement(self.name + measurement)
            for constraint, parameters in m_obj._constraints:
                ppos = [self.observables.index(p) for p in parameters]
                # construct the theory covariance
                cov_sm_p = cov_sm[ppos][:,ppos]
                if len(ppos) == 1:
                    # if there is only one observable: univariate Gaussian
                    std = np.sqrt(cov_sm_p[0,0])
                    if std == 0:
                        constraint_th = DeltaDistribution(constraint.central_value)
                    else:
                        constraint_th = NormalDistribution(
                                    constraint.central_value, std)
                else:
                    # if there is more than one observable: multivariate Gaussian
                    # now here's a challenge: for some observables, the theory
                    # uncertainty might be zero. To fix this, we can replace it
                    # by a Gaussian with a very small std deviation. To get an
                    # idea what "small" is, we look at the support of the
                    # experimental constraint.
                    index_zero = np.where(np.diag(cov_sm_p) == 0)[0]
                    cov_sm_p_fixed = cov_sm_p
                    for i in index_zero:
                        r = np.diff(constraint.support.T[1])[0]
                        cov_sm_p_fixed[i, i] = (r/100000.)**2 # small
                    constraint_th = MultivariateNormalDistribution(
                                    constraint.central_value, cov_sm_p_fixed)
                constraint_comb = convolve_distributions([constraint, constraint_th])
                pm.add_constraint(parameters, constraint_comb)

    def array_to_dict(self, x):
        """Convert a 1D numpy array of floats to a dictionary of fit parameters,
        nuisance parameters, and Wilson coefficients."""
        d = {}
        n_fit_p = len(self.fit_parameters)
        n_wc = len(self.fit_wc_names)
        d['fit_parameters'] = { p: x[i] for i, p in enumerate(self.fit_parameters) }
        d['fit_wc'] = { p: x[i + n_fit_p] for i, p in enumerate(self.fit_wc_names) }
        return d

    def dict_to_array(self, d):
        """Convert a dictionary of fit parameters and Wilson coefficients to a
        1D numpy array of floats."""
        n_fit_p = len(self.fit_parameters)
        n_wc = len(self.fit_wc_names)
        arr = np.zeros(n_fit_p + n_nui_p + n_wc)
        arr[:n_fit_p] = [d['fit_parameters'][p] for p in self.fit_parameters]
        arr[n_fit_p:]   = [d['fit_wc'][c] for c in self.fit_wc_names]
        return arr

    def get_par_dict(self, x):
        d = self.array_to_dict(x)
        par_dict = self.parameters_central.copy()
        par_dict.update(d['fit_parameters'])
        return par_dict

    def log_likelihood(self, x):
        """Return the logarithm of the likelihood. Note that there is no prior
        probability for nuisance parameters, which have been integrated out.
        Priors for fit parameters are ignored."""
        predictions = self.get_predictions(x)
        ll = 0.
        for measurement in self.get_measurements:
            m_obj = flavio.classes.PseudoMeasurement.get_instance(self.name + measurement)
            m_obs = m_obj.all_parameters
            exclude_observables = set(m_obs) - set(self.observables)
            prob_dict = m_obj.get_logprobability_all(predictions, exclude_parameters=exclude_observables)
            ll += sum(prob_dict.values())
        return ll
