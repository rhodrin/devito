import numpy as np
from cached_property import cached_property

from devito.interfaces import DenseData, TimeData
from examples.acoustic.fwi_operators import (
    ForwardOperator, AdjointOperator, GradientOperator, BornOperator
)
from examples.seismic import PointSource, Receiver


class AcousticWaveSolver(object):
    """
    Solver object that provides operators for seismic inversion problems
    and encapsulates the time and space discretization for a given problem
    setup.

    :param model: Physical model with domain parameters
    :param source: Sparse point symbol providing the injected wave
    :param receiver: Sparse point symbol describing an array of receivers
    :param time_order: Order of the time-stepping scheme (default: 2)
    :param space_order: Order of the spatial stencil discretisation (default: 4)

    Note: space_order must always be greater than time_order
    """
    def __init__(self, model, source, receiver,
                 time_order=2, space_order=2, **kwargs):
        self.model = model
        self.source = source
        self.receiver = receiver

        self.time_order = time_order
        self.space_order = space_order

        # Time step can be \sqrt{3}=1.73 bigger with 4th order
        self.dt = self.model.critical_dt
        if self.time_order == 4:
            self.dt *= 1.73

        # Cache compiler options
        self._kwargs = kwargs

    @cached_property
    def op_fwd(self):
        """Cached operator for forward runs with buffered wavefield"""
        return ForwardOperator(self.model, save=False, source=self.source,
                               receiver=self.receiver, time_order=self.time_order,
                               space_order=self.space_order, **self._kwargs)

    @cached_property
    def op_fwd_save(self):
        """Cached operator for forward runs with unrolled wavefield"""
        return ForwardOperator(self.model, save=True, source=self.source,
                               receiver=self.receiver, time_order=self.time_order,
                               space_order=self.space_order, **self._kwargs)

    @property
    def op_adj(self):
        """Cached operator for adjoint runs"""
        return AdjointOperator(self.model, save=False, source=self.source,
                               receiver=self.receiver, time_order=self.time_order,
                               space_order=self.space_order, **self._kwargs)

    @property
    def op_grad(self):
        """Cached operator for gradient runs"""
        return GradientOperator(self.model, save=False, source=self.source,
                                receiver=self.receiver, time_order=self.time_order,
                                space_order=self.space_order, **self._kwargs)

    @property
    def op_born(self):
        """Cached operator for born runs"""
        return BornOperator(self.model, save=False, source=self.source,
                            receiver=self.receiver, time_order=self.time_order,
                            space_order=self.space_order, **self._kwargs)

    def forward(self, src=None, rec=None, u=None, m=None, save=False, **kwargs):
        """
        Forward modelling function that creates the necessary
        data objects for running a forward modelling operator.

        :param src: Symbol with time series data for the injected source term
        :param rec: Symbol to store interpolated receiver data
        :param u: (Optional) Symbol to store the computed wavefield
        :param m: (Optional) Symbol for the time-constant square slowness
        :param save: Option to store the entire (unrolled) wavefield

        :returns: Receiver, wavefield and performance summary
        """
        # Source term is read-only, so re-use the default
        src = src or self.source
        # Create a new receiver object to store the result
        rec = rec or Receiver(name='rec', ntime=self.receiver.nt,
                              coordinates=self.receiver.coordinates.data)

        # Create the forward wavefield if not provided
        u = u or TimeData(name='u', shape=self.model.shape_domain,
                          save=save, time_dim=self.source.nt,
                          time_order=self.time_order,
                          space_order=self.space_order,
                          dtype=self.model.dtype)

        # Pick m from model unless explicitly provided
        m = m or self.model.m

        # Execute operator and return wavefield and receiver data
        if save:
            summary = self.op_fwd_save.apply(src=src, rec=rec, u=u, m=m, **kwargs)
        else:
            summary = self.op_fwd.apply(src=src, rec=rec, u=u, m=m, **kwargs)
        return rec, u, summary

    def adjoint(self, rec, srca=None, v=None, m=None, **kwargs):
        """
        Adjoint modelling function that creates the necessary
        data objects for running an adjoint modelling operator.

        :param rec: Symbol with stored receiver data. Please note that
                    these act as the source term in the adjoint run.
        :param srca: Symbol to store the resulting data for the
                     interpolated at the original source location.
        :param v: (Optional) Symbol to store the computed wavefield
        :param m: (Optional) Symbol for the time-constant square slowness

        :returns: Adjoint source, wavefield and performance summary
        """
        # Create a new adjoint source and receiver symbol
        srca = srca or PointSource(name='srca', ntime=self.source.nt,
                                   coordinates=self.source.coordinates.data)

        # Create the adjoint wavefield if not provided
        v = v or TimeData(name='v', shape=self.model.shape_domain,
                          save=False, time_order=self.time_order,
                          space_order=self.space_order,
                          dtype=self.model.dtype)

        # Pick m from model unless explicitly provided
        m = m or self.model.m

        # Execute operator and return wavefield and receiver data
        summary = self.op_adj.apply(srca=srca, rec=rec, v=v, m=m, **kwargs)
        return srca, v, summary

    def gradient(self, recin, u, v=None, grad=None, m=None, **kwargs):
        """
        Gradient modelling function for computing the adjoint of the
        Linearized Born modelling function, ie. the action of the
        Jacobian adjoint on an input data.

        :param recin: Receiver data as a numpy array
        :param u: Symbol for full wavefield `u` (created with save=True)
        :param v: (Optional) Symbol to store the computed wavefield
        :param grad: (Optional) Symbol to store the gradient field

        :returns: Gradient field and performance summary
        """
        # Create receiver symbol with the provided data
        rec = Receiver(name='rec', data=recin,
                       coordinates=self.receiver.coordinates.data)

        # Gradient symbol
        grad = grad or DenseData(name='grad', shape=self.model.shape_domain,
                                 dtype=self.model.dtype)

        # Create the forward wavefield
        v = v or TimeData(name='v', shape=self.model.shape_domain,
                          save=False, time_dim=self.source.nt,
                          time_order=self.time_order,
                          space_order=self.space_order,
                          dtype=self.model.dtype)

        # Pick m from model unless explicitly provided
        m = m or self.model.m

        summary = self.op_grad.apply(rec=rec, grad=grad, v=v, u=u, m=m, **kwargs)
        return grad, summary

    def born(self, dmin, src=None, rec=None, u=None, U=None, m=None, **kwargs):
        """
        Linearized Born modelling function that creates the necessary
        data objects for running an adjoint modelling operator.

        :param src: Symbol with time series data for the injected source term
        :param rec: Symbol to store interpolated receiver data
        :param u: (Optional) Symbol to store the computed wavefield
        :param U: (Optional) Symbol to store the computed wavefield
        :param m: (Optional) Symbol for the time-constant square slowness
        """
        # Source term is read-only, so re-use the default
        src = src or self.source
        # Create a new receiver object to store the result
        rec = rec or Receiver(name='rec', ntime=self.receiver.nt,
                              coordinates=self.receiver.coordinates.data)

        # Create the forward wavefields u and U if not provided
        u = u or TimeData(name='u', shape=self.model.shape_domain,
                          save=False, time_order=self.time_order,
                          space_order=self.space_order,
                          dtype=self.model.dtype)
        U = U or TimeData(name='U', shape=self.model.shape_domain,
                          save=False, time_order=self.time_order,
                          space_order=self.space_order, dtype=self.model.dtype)

        # Pick m from model unless explicitly provided
        m = m or self.model.m

        if isinstance(dmin, np.ndarray):
            dm = DenseData(name='dm', shape=self.model.shape_domain,
                           dtype=self.model.dtype)
            dm.data[:] = dmin
        else:
            dm = dmin

        # execute operator and return wavefield and receiver data
        summary = self.op_born.apply(dm=dm, u=u, U=U, src=src, rec=rec,
                                     m=m, **kwargs)
        return rec.data, u, U, summary
