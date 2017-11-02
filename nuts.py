"""
This package implements the No-U-Turn Sampler (NUTS) algorithm 6 from the NUTS
paper (Hoffman & Gelman, 2011).

Content
-------

The package mainly contains:
  nuts6                     return samples using the NUTS
  test_nuts6                example usage of this package

and subroutines of nuts6:
  build_tree                the main recursion in NUTS
  find_reasonable_epsilon   Heuristic for choosing an initial value of epsilon
  leapfrog                  Perfom a leapfrog jump in the Hamiltonian space
  stop_criterion            Compute the stop condition in the main loop


A few words about NUTS
----------------------

Hamiltonian Monte Carlo or Hybrid Monte Carlo (HMC) is a Markov chain Monte
Carlo (MCMC) algorithm that avoids the random walk behavior and sensitivity to
correlated parameters, biggest weakness of many MCMC methods. Instead, it takes
a series of steps informed by first-order gradient information.

This feature allows it to converge much more quickly to high-dimensional target
distributions compared to simpler methods such as Metropolis, Gibbs sampling
(and derivatives).

However, HMC's performance is highly sensitive to two user-specified
parameters: a step size, and a desired number of steps.  In particular, if the
number of steps is too small then the algorithm will just exhibit random walk
behavior, whereas if it is too large it will waste computations.

Hoffman & Gelman introduced NUTS or the No-U-Turn Sampler, an extension to HMC
that eliminates the need to set a number of steps.  NUTS uses a recursive
algorithm to find likely candidate points that automatically stops when it
starts to double back and retrace its steps.  Empirically, NUTS perform at
least as effciently as and sometimes more effciently than a well tuned standard
HMC method, without requiring user intervention or costly tuning runs.

Moreover, Hoffman & Gelman derived a method for adapting the step size
parameter on the fly based on primal-dual averaging.  NUTS can thus be used
with no hand-tuning at all.

In practice, the implementation still requires a number of steps, a burning
period and a stepsize. However, the stepsize will be optimized during the
burning period, and the final values of all the user-defined values will be
revised by the algorithm.

reference: arXiv:1111.4246
"The No-U-Turn Sampler: Adaptively Setting Path Lengths in Hamiltonian Monte
Carlo", Matthew D. Hoffman & Andrew Gelman
"""
import numpy as np
import sys
from numpy import log, exp, sqrt

__all__ = ['nuts6']


def leapfrog(theta, r, grad, epsilon, f, inv_mass):
    """ Perfom a leapfrog jump in the Hamiltonian space
    INPUTS
    ------
    theta: ndarray[float, ndim=1]
        initial parameter position

    r: ndarray[float, ndim=1]
        initial momentum

    grad: float
        initial gradient value

    epsilon: float
        step size

    f: callable
        it should return the log probability and gradient evaluated at theta
        logp, grad = f(theta)

    OUTPUTS
    -------
    thetaprime: ndarray[float, ndim=1]
        new parameter position
    rprime: ndarray[float, ndim=1]
        new momentum
    gradprime: float
        new gradient
    logpprime: float
        new lnp
    """
    # make half step in r
    rprime = r + 0.5 * epsilon * grad
    # make new step in theta
    thetaprime = theta + epsilon * inv_mass * rprime
    #compute new gradient
    logpprime, gradprime = f(thetaprime)
    # make half step in r again
    rprime = rprime + 0.5 * epsilon * gradprime
    return thetaprime, rprime, gradprime, logpprime


def find_reasonable_epsilon(theta0, grad0, logp0, f, epsilon0, inv_mass=0):
    """ Heuristic for choosing an initial value of epsilon """
    epsilon = float(1.0*epsilon0)
    r0 = np.random.normal(0., 1., len(theta0))

    # Figure out what direction we should be moving epsilon.
    _, rprime, gradprime, logpprime = leapfrog(theta0, r0, grad0, epsilon, f, inv_mass)
    # brutal! This trick make sure the step is not huge leading to infinite
    # values of the likelihood. This could also help to make sure theta stays
    # within the prior domain (if any)
    k = 1.
    while not (np.isfinite(logpprime) and np.isfinite(gradprime).all()):
        k *= 0.5
        _, rprime, gradprime, logpprime = leapfrog(theta0, r0, grad0, epsilon * k, f, inv_mass)

    epsilon = 0.5 * k * epsilon

    logacceptprob = logpprime - logp0 - 0.5 * (np.dot(rprime, (inv_mass*rprime).T) - np.dot(r0, (inv_mass*r0).T))

    a = 2. * float((exp(logacceptprob) > 0.5)) - 1.
    # Keep moving epsilon in that direction until acceptprob crosses 0.5.
    while (a*logacceptprob > -a*np.log(2.)):
        epsilon = epsilon * (2. ** a)
        _, rprime, _, logpprime = leapfrog(theta0, r0, grad0, epsilon, f, inv_mass)
        logacceptprob = logpprime - logp0 - 0.5 * ( np.dot(rprime, (inv_mass*rprime).T) - np.dot(r0, (inv_mass*r0).T))
        # print('logacceptprob = {0}'.format(logacceptprob))

    # print("find_reasonable_epsilon=", epsilon)

    return epsilon


def stop_criterion(thetaminus, thetaplus, rminus, rplus, inv_mass):
    """ Compute the stop condition in the main loop
    dot(dtheta, rminus) >= 0 & dot(dtheta, rplus >= 0)

    INPUTS
    ------
    thetaminus, thetaplus: ndarray[float, ndim=1]
        under and above position
    rminus, rplus: ndarray[float, ndim=1]
        under and above momentum

    OUTPUTS
    -------
    criterion: bool
        return if the condition is valid
    """
    dtheta = thetaplus - thetaminus
    return (np.dot(dtheta, (inv_mass*rminus).T) >= 0) & (np.dot(dtheta, (inv_mass*rplus).T) >= 0)


def build_tree(theta, r, grad, logu, v, j, epsilon, f, joint0, inv_mass_chol, inv_mass):
    """The main recursion."""
    if (j == 0):
        # Base case: Take a single leapfrog step in the direction v.
        thetaprime, rprime, gradprime, logpprime = leapfrog(theta, r, grad, v * epsilon, f, inv_mass)
        invM_cholrprime = inv_mass_chol * rprime
        joint = float(logpprime) - 0.5 * np.dot(invM_cholrprime, invM_cholrprime.T)
        # Is the new point in the slice?
        nprime = int(logu < joint)
        # Is the simulation wildly inaccurate?
        sprime = np.isfinite(joint) and int((logu - 1000.) < joint)
        # Set the return values---minus=plus for all things here, since the
        # "tree" is of depth 0.
        thetaminus = thetaprime[:]
        thetaplus = thetaprime[:]
        rminus = rprime[:]
        rplus = rprime[:]
        gradminus = gradprime[:]
        gradplus = gradprime[:]
        # Compute the acceptance probability.
        if not np.isfinite(joint):
            alphaprime = 0
        else:
            alphaprime = min(1., np.exp(joint - joint0))
            #alphaprime = min(1., np.exp(logpprime - 0.5 * np.dot(rprime, rprime.T) - joint0))
        nalphaprime = 1
    else:
        # Recursion: Implicitly build the height j-1 left and right subtrees.
        thetaminus, rminus, gradminus, thetaplus, rplus, gradplus, thetaprime, gradprime, logpprime, nprime, sprime, alphaprime, nalphaprime = build_tree(theta, r, grad, logu, v, j - 1, epsilon, f, joint0, inv_mass_chol, inv_mass)
        # No need to keep going if the stopping criteria were met in the first subtree.
        if (sprime == 1):
            if (v == -1):
                thetaminus, rminus, gradminus, _, _, _, thetaprime2, gradprime2, logpprime2, nprime2, sprime2, alphaprime2, nalphaprime2 = build_tree(thetaminus, rminus, gradminus, logu, v, j - 1, epsilon, f, joint0, inv_mass_chol, inv_mass)
            else:
                _, _, _, thetaplus, rplus, gradplus, thetaprime2, gradprime2, logpprime2, nprime2, sprime2, alphaprime2, nalphaprime2 = build_tree(thetaplus, rplus, gradplus, logu, v, j - 1, epsilon, f, joint0, inv_mass_chol, inv_mass)
            # Choose which subtree to propagate a sample up from.
            if (np.random.uniform() < (float(nprime2) / max(float(int(nprime) + int(nprime2)), 1.))):
                thetaprime = thetaprime2[:]
                gradprime = gradprime2[:]
                logpprime = logpprime2
            # Update the number of valid points.
            nprime = int(nprime) + int(nprime2)
            # Update the stopping criterion.
            sprime = int(sprime and sprime2 and stop_criterion(thetaminus, thetaplus, rminus, rplus, inv_mass))
            # Update the acceptance probability statistics.
            alphaprime = alphaprime + alphaprime2
            nalphaprime = nalphaprime + nalphaprime2

    return thetaminus, rminus, gradminus, thetaplus, rplus, gradplus, thetaprime, gradprime, logpprime, nprime, sprime, alphaprime, nalphaprime


def nuts6(f, M, Madapt, theta0, delta=0.6, max_tree_depth=10, epsilon0=1.0, estimate_mass=True, log_file=sys.stdout):
    """
    Implements the No-U-Turn Sampler (NUTS) algorithm 6 from from the NUTS
    paper (Hoffman & Gelman, 2011).

    Runs Madapt steps of burn-in, during which it adapts the step size
    parameter epsilon, then starts generating samples to return.

    Note the initial step size is tricky and not exactly the one from the
    initial paper.  In fact the initial step size could be given by the user in
    order to avoid potential problems

    INPUTS
    ------
    epsilon: float
        step size
        see nuts8 if you want to avoid tuning this parameter

    f: callable
        it should return the log probability and gradient evaluated at theta
        logp, grad = f(theta)

    M: int
        number of samples to generate.

    Madapt: int
        the number of steps of burn-in/how long to run the dual averaging
        algorithm to fit the step size epsilon.

    theta0: ndarray[float, ndim=1]
        initial guess of the parameters.

    KEYWORDS
    --------
    delta: float
        targeted acceptance fraction

    OUTPUTS
    -------
    samples: ndarray[float, ndim=2]
    M x D matrix of samples generated by NUTS.
    note: samples[0, :] = theta0
    """

    if len(np.shape(theta0)) > 1:
        raise ValueError('theta0 is expected to be a 1-D array')

    D = len(theta0)
    samples = np.empty((M + Madapt, D), dtype=float)
    lnprob = np.empty(M + Madapt, dtype=float)

    logp, grad = f(theta0)
    samples[0, :] = theta0
    lnprob[0] = logp

    if estimate_mass:
        alpha = 0.8
        diag_mass_avg = alpha*grad[:]**2 + (1-alpha)
        diag_mass_N = 1

        mass_chol = np.sqrt(diag_mass_avg)
        inv_mass = 1.0/diag_mass_avg
        inv_mass_chol = 1.0/mass_chol
    else:
        mass_chol = 1.0
        inv_mass = 1.0
        inv_mass_chol = 1.0

    # Choose a reasonable first epsilon by a simple heuristic.
    epsilon = find_reasonable_epsilon(theta0, grad, logp, f, epsilon0, inv_mass)
    logepsilon = log(epsilon)

    # Parameters to the dual averaging algorithm.
    gamma = 0.05
    t0 = 10
    kappa = 0.75
    mu = log(10. * epsilon)

    # Initialize dual averaging algorithm.
    logepsilonbar = 0
    Hbar = 0
    m_adapt = 1

    for m in range(1, M + Madapt):
        # Resample momenta.
        invM_cholr0 = np.random.normal(0, 1, D)
        r0 = mass_chol * invM_cholr0

        #joint lnp of theta and momentum r
        joint = logp - 0.5 * np.dot(invM_cholr0, invM_cholr0.T)

        # Resample u ~ uniform([0, exp(joint)]).
        # Equivalent to (log(u) - joint) ~ exponential(1).
        logu = float(joint - np.random.exponential(1, size=1))

        # if all fails, the next sample will be the previous one
        samples[m, :] = samples[m - 1, :]
        lnprob[m] = lnprob[m - 1]

        # initialize the tree
        thetaminus = samples[m - 1, :]
        thetaplus = samples[m - 1, :]
        rminus = r0[:]
        rplus = r0[:]
        gradminus = grad[:]
        gradplus = grad[:]

        j = 0  # initial heigth j = 0
        n = 1  # Initially the only valid point is the initial point.
        s = 1  # Main loop: will keep going until s == 0.

        while (s == 1):
            # Choose a direction. -1 = backwards, 1 = forwards.
            v = int(2 * (np.random.uniform() < 0.5) - 1)

            # Double the size of the tree.
            if (v == -1):
                thetaminus, rminus, gradminus, _, _, _, thetaprime, gradprime, logpprime, nprime, sprime, alpha, nalpha = build_tree(thetaminus, rminus, gradminus, logu, v, j, epsilon, f, joint, inv_mass_chol, inv_mass)
            else:
                _, _, _, thetaplus, rplus, gradplus, thetaprime, gradprime, logpprime, nprime, sprime, alpha, nalpha = build_tree(thetaplus, rplus, gradplus, logu, v, j, epsilon, f, joint, inv_mass_chol, inv_mass)

            # Use Metropolis-Hastings to decide whether or not to move to a
            # point from the half-tree we just generated.
            _tmp = min(1, float(nprime) / float(n))
            if (sprime == 1) and (np.random.uniform() < _tmp):
                samples[m, :] = thetaprime[:]
                lnprob[m] = logpprime
                logp = logpprime
                grad = gradprime[:]
            # Update number of valid points we've seen.
            n += nprime
            # Increment depth.
            j += 1
            # Decide if it's time to stop.
            s = sprime and stop_criterion(thetaminus, thetaplus, rminus, rplus, inv_mass) and j < max_tree_depth

        print('It {0}: logp = {1:.2f}, j = {2}, E[accept]= {3:.4f}, epsilon = {4:.4g}'.format(m,lnprob[m],j,alpha/float(nalpha),epsilon),
              file=log_file,flush=True)
        if (m <= Madapt):
            # Do adaptation of epsilon if we're still doing burn-in.
            eta = 1. / float(m_adapt + t0)
            Hbar = (1. - eta) * Hbar + eta * (delta - alpha / float(nalpha))
            logepsilon = mu - sqrt(m_adapt) / gamma * Hbar
            eta = m_adapt ** -kappa
            logepsilonbar = (1. - eta) * logepsilonbar + eta * logepsilon
            m_adapt += 1

            # update mass matrix approximation
            if estimate_mass:
                diag_mass_N += 1
                diag_mass_avg += (grad[:]**2 - diag_mass_avg)/diag_mass_N

                mass_chol = np.sqrt(diag_mass_avg)
                inv_mass = 1.0/diag_mass_avg
                inv_mass_chol = 1.0/mass_chol

            if m == int(0.25*Madapt) or m == int(0.75*Madapt):
                # Reinitialize dual averaging algorithm.
                logepsilon = logepsilonbar
                logepsilonbar = 0
                Hbar = 0
                m_adapt = 1

            if m == int(0.5*Madapt):
                # Reinitialize mass matrix estimation
                diag_mass_N = 1
        else:
            logepsilon = logepsilonbar
        epsilon = exp(logepsilon)

    samples = samples[Madapt:, :]
    lnprob = lnprob[Madapt:]
    return samples, lnprob, (epsilon,inv_mass)


def test_nuts6():
    """ Example usage of nuts6: sampling a 2d highly correlated Gaussian distribution """

    def correlated_normal(theta):
        """
        Example of a target distribution that could be sampled from using NUTS.
        (Although of course you could sample from it more efficiently)
        Doesn't include the normalizing constant.
        """

        # Precision matrix with covariance [1, 1.98; 1.98, 4].
        # A = np.linalg.inv( cov )
        A = np.asarray([[50.251256, -24.874372],
                        [-24.874372, 12.562814]])

        grad = -np.dot(theta, A)
        logp = 0.5 * np.dot(grad, theta.T)
        return logp, grad

    D = 2
    M = 5000
    Madapt = 5000
    theta0 = np.random.normal(0, 1, D)
    delta = 0.2

    mean = np.zeros(2)
    cov = np.asarray([[1, 1.98],
                      [1.98, 4]])

    print('Running HMC with dual averaging and trajectory length %0.2f...' % delta)
    samples, lnprob, epsilon = nuts6(correlated_normal, M, Madapt, theta0, delta)
    print('Done. Final epsilon = %f.' % epsilon)

    samples = samples[1::10, :]
    print('Percentiles')
    print (np.percentile(samples, [16, 50, 84], axis=0))
    print('Mean')
    print (np.mean(samples, axis=0))
    print('Stddev')
    print (np.std(samples, axis=0))

    import pylab as plt
    temp = np.random.multivariate_normal(mean, cov, size=500)
    plt.plot(temp[:, 0], temp[:, 1], '.')
    plt.plot(samples[:, 0], samples[:, 1], 'r+')
    plt.show()
