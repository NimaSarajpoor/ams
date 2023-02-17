"""
Checks for dispatchable loads.
"""

from ams.solver.pypower.idx_gen import PMAX, PMIN


def isload(gen):
    """Checks for dispatchable loads.

    Returns a column vector of 1's and 0's. The 1's correspond to rows of the
    C{gen} matrix which represent dispatchable loads. The current test is
    C{Pmin < 0 and Pmax == 0}. This may need to be revised to allow sensible
    specification of both elastic demand and pumped storage units.

    @author: Ray Zimmerman (PSERC Cornell)
    """
    return (gen[:, PMIN] < 0) & (gen[:, PMAX] == 0)
