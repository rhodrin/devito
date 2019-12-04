from collections import OrderedDict

from devito.archinfo import Platform
from devito.ir.iet import Node
from devito.logger import dle as log, dle_warning as warning
from devito.parameters import configuration
from devito.targets.common import Graph
from devito.tools import Singleton

__all__ = ['dle_registry', 'iet_lower', 'targets', 'Target']


dle_registry = ('noop', 'advanced')


class TargetsMap(OrderedDict, metaclass=Singleton):

    #TODO: update this

    """
    The DLE transformation modes. This is a dictionary ``P -> {M -> R}``,
    where P is a Platform, M a rewrite mode (e.g., 'advanced'),
    and R a Target.

    This dictionary is to be modified at backend-initialization time by adding
    all Platform-keyed mappers as supported by the specific backend.
    """

    def add(self, platform, mapper):
        assert issubclass(platform, Platform)
        assert isinstance(mapper, dict)
        assert all(i in mapper for i in dle_registry)
        super(TargetsMap, self).__setitem__(platform, mapper)

    def fetch(self, platform, mode):
        # Try to fetch the most specific Target for the given Platform
        for cls in platform.__class__.mro():
            for k, v in self.items():
                if issubclass(k, cls):
                    return v[mode]
        raise KeyError("Couldn't find a rewriter `%s` for platform `%s`"
                       % (mode, platform))


targets = TargetsMap()
"""To be populated by the individual backends."""


class Target(object):

    """
    Specialize an Iteration/Expression tree (IET) introducing Target-specific
    optimizations, parallelism, etc.
    """

    def __init__(self, params, platform):
        self.params = params
        self.platform = platform

    def process(self, iet):
        graph = Graph(iet)

        self._pipeline(graph)

        return graph

    def _pipeline(self, graph):
        """The rewrite passes."""
        return


def iet_lower(iet, mode='advanced', options=None):
    """
    Specialize an Iteration/Expression tree (IET) for a certain Target. This
    consists of:

        * Loop-level transformations to generate Target-optimized code;
        * Shared-memory and distributed-memory parallelism;
        * Symbol and data management.

    Parameters
    ----------
    iet : Node
        The IET to be lowered.
    mode : str, optional
        The DLE transformation mode.
        - ``noop``: Do nothing.
        - ``advanced``: flush denormals, vectorization, loop blocking, OpenMP-related
                        optimizations (collapsing, nested parallelism, ...), MPI-related
                        optimizations (aggregation of communications, reshuffling of
                        communications, ...).
    options : dict, optional
        - ``openmp``: Enable/disable OpenMP. Defaults to `configuration['openmp']`.
        - ``mpi``: Enable/disable MPI. Defaults to `configuration['mpi']`.
        - ``blockinner``: Enable/disable blocking of innermost loops. By default,
                          this is disabled to maximize SIMD vectorization. Pass True
                          to override this heuristic.
        - ``blocklevels``: Levels of blocking for hierarchical tiling (blocks,
                           sub-blocks, sub-sub-blocks, ...). Different Platforms have
                           different default values.
        - ``language``: To generate code using a low-level language other than the
                        default one. Currently this option is ignored, but in the
                        future we expect to have multiple alternatives for the same
                        platform. For example, if the platform in question is NVIDIAX,
                        one may want to generate OpenMP or OpenACC offloading code --
                        ``language=openmp`` and ``language=openacc`` respectively) --
                        rather than the default ``language=cuda``.
    """
    assert isinstance(iet, Node)

    # What is the target platform for which the optimizations are applied?
    platform = configuration['platform']

    # Default options
    params = {}
    params['blockinner'] = configuration['dle-options'].get('blockinner', False)
    params['blocklevels'] = configuration['dle-options'].get('blocklevels', None)
    params['openmp'] = configuration['openmp']
    params['mpi'] = configuration['mpi']

    # Parse input options (potentially replacing defaults)
    for k, v in (options or {}).items():
        if k not in params:
            warning("Illegal DLE option '%s'" % k)
        else:
            params[k] = v

    # Force OpenMP/MPI if parallelism was requested, even though mode is 'noop'
    if mode is None:
        mode = 'noop'
    elif mode == 'noop':
        mode = tuple(i for i in ['mpi', 'openmp'] if params[i]) or 'noop'

    # Fetch the requested rewriter
    try:
        rewriter = targets.fetch(platform, mode)(params, platform)
    except KeyError:
        # Fallback: custom rewriter -- `mode` is a specific sequence of
        # transformation passes
        rewriter = targets.fetch(platform, 'custom')(mode, params, platform)

    # Trigger the DLE passes
    graph = rewriter.process(iet)

    # Print out the profiling data
    print_profiling(graph)

    return graph.root, graph


def print_profiling(graph):
    """
    Print a summary of the applied transformations.
    """
    timings = graph.timings

    if configuration['profiling'] in ['basic', 'advanced']:
        row = "%s (elapsed: %.2f s)"
        out = "\n     ".join(row % ("".join(filter(lambda c: not c.isdigit(), k)), v)
                             for k, v in timings.items())
        elapsed = sum(timings.values())
        log("%s\n     [Total elapsed: %.2f s]" % (out, elapsed))
    else:
        # Shorter summary
        log("passes: %s (elapsed %.2f s)" % (",".join(timings), sum(timings.values())))