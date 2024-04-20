"""
Module for system matrix make.
"""

import logging
from typing import Optional

import numpy as np

from scipy.sparse import csr_matrix as c_sparse
from scipy.sparse import csc_matrix as csc_sparse
from scipy.sparse import lil_matrix as l_sparse

from andes.utils.misc import elapsed

from ams.opt.omodel import Param

logger = logging.getLogger(__name__)


class MParam(Param):
    """
    Class for matrix parameters built from the system.

    MParam is designed to be a subclass of RParam for routine parameters
    management.

    Parameters
    ----------
    name : str, optional
        Name of this parameter. If not provided, `name` will be set
        to the attribute name.
    tex_name : str, optional
        LaTeX-formatted parameter name. If not provided, `tex_name`
        will be assigned the same as `name`.
    info : str, optional
        A description of this parameter
    unit : str, optional
        Unit of the parameter.
    v : np.ndarray, optional
        Matrix value of the parameter.
    owner : object, optional
        Owner of the MParam, usually the MatProcessor instance.
    """

    def __init__(self,
                 name: Optional[str] = None,
                 tex_name: Optional[str] = None,
                 info: Optional[str] = None,
                 unit: Optional[str] = None,
                 v: Optional[np.ndarray] = None,
                 sparse: Optional[bool] = False,
                 ):
        self.name = name
        self.tex_name = tex_name if (tex_name is not None) else name
        self.info = info
        self.unit = unit
        self._v = v
        self.sparse = sparse
        self.owner = None

    @property
    def v(self):
        """
        Return the value of the parameter.
        """
        # NOTE: scipy.sparse matrix will return 2D array
        # so we squeeze it here if only one row
        if isinstance(self._v, (c_sparse, l_sparse, csc_sparse)):
            out = self._v.toarray()
            if out.shape[0] == 1:
                return np.squeeze(out)
            else:
                return out
        return self._v

    @property
    def shape(self):
        """
        Return the shape of the parameter.
        """
        return self.v.shape

    @property
    def n(self):
        """
        Return the szie of the parameter.
        """
        return len(self.v)

    @property
    def class_name(self):
        """
        Return the class name
        """
        return self.__class__.__name__


class MatProcessor:
    """
    Class for matrix processing in AMS system.
    """

    def __init__(self, system):
        self.system = system
        self.initialized = False
        self.Bbus = MParam(name='Bbus', tex_name=r'B_{bus}',
                           info='Bus admittance matrix',
                           v=None, sparse=True)
        self.Bf = MParam(name='Bf', tex_name=r'B_{f}',
                         info='Bf matrix',
                         v=None, sparse=True)
        self.Pbusinj = MParam(name='Pbusinj', tex_name=r'P_{bus}^{inj}',
                              info='Bus power injection vector',
                              v=None,)
        self.Pfinj = MParam(name='Pfinj', tex_name=r'P_{f}^{inj}',
                            info='Line power injection vector',
                            v=None,)
        self.PTDF = MParam(name='PTDF', tex_name=r'P_{TDF}',
                           info='Power transfer distribution factor',
                           v=None)

        self.Cft = MParam(name='Cft', tex_name=r'C_{ft}',
                          info='Line connectivity matrix',
                          v=None, sparse=True)
        self.CftT = MParam(name='CftT', tex_name=r'C_{ft}^{T}',
                           info='Line connectivity matrix transpose',
                           v=None, sparse=True)
        self.Cg = MParam(name='Cg', tex_name=r'C_g',
                         info='Generator connectivity matrix',
                         v=None, sparse=True)
        self.Cl = MParam(name='Cl', tex_name=r'Cl',
                         info='Load connectivity matrix',
                         v=None, sparse=True)
        self.Csh = MParam(name='Csh', tex_name=r'C_{sh}',
                          info='Shunt connectivity matrix',
                          v=None, sparse=True)

    def build(self):
        """
        Build the system matrices.
        It includes connectivity matrices: Cg, Cl, Csh, Cft, and CftT,
        and bus matrices: Bbus, Bf, Pbusinj, and Pfinj.
        """
        t_mat, _ = elapsed()
        # --- connectivity matrices ---
        _ = self.build_cg()
        _ = self.build_cl()
        _ = self.build_csh()
        _ = self.build_cft()
        # --- bus matrices ---
        self._makeBdc()
        _, s_mat = elapsed(t_mat)
        logger.debug(f"Built system matrices in {s_mat}.")
        self.initialized = True
        return True

    @property
    def class_name(self):
        """
        Return the class name
        """
        return self.__class__.__name__

    @property
    def n(self):
        """
        To fit the RParam style.
        """
        return 2

    def _makeBdc(self):
        """
        Make Bdc matrix.

        Call _makeC() before this method to ensure Cft is available.
        """
        system = self.system

        # common variables
        nb = system.Bus.n
        nl = system.Line.n

        # line parameters
        idx_line = system.Line.idx.v
        x = system.Line.get(src='x', attr='v', idx=idx_line)
        u_line = system.Line.get(src='u', attr='v', idx=idx_line)
        b = u_line / x  # series susceptance

        # in DC, tap is assumed to be 1
        tap0 = system.Line.get(src='tap', attr='v', idx=idx_line)
        tap = np.ones(nl)
        i = np.flatnonzero(tap0)
        tap[i] = tap0[i]  # assign non-zero tap ratios
        b = b / tap  # adjusted series susceptance

        # build Bf such that Bf * Va is the vector of real branch powers injected
        # at each branch's "from" bus
        f = system.Bus.idx2uid(system.Line.get(src='bus1', attr='v', idx=idx_line))
        t = system.Bus.idx2uid(system.Line.get(src='bus2', attr='v', idx=idx_line))
        ir = np.r_[range(nl), range(nl)]  # double set of row indices
        Bf = c_sparse((np.r_[b, -b], (ir, np.r_[f, t])), (nl, nb))

        # build Cft, note that this Cft is different from the one in _makeC()
        Cft = c_sparse((np.r_[np.ones(nl), -np.ones(nl)], (ir, np.r_[f, t])), (nl, nb))

        # build Bbus
        Bbus = Cft.T * Bf

        phi = system.Line.get(src='phi', attr='v', idx=idx_line)
        Pfinj = b * (-phi)
        Pbusinj = Cft.T * Pfinj

        self.Bbus._v, self.Bf._v, self.Pbusinj._v, self.Pfinj._v = Bbus, Bf, Pbusinj, Pfinj
        return True

    def build_cg(self):
        """
        Build generator connectivity matrix Cg, and store it in the MParam `Cg`.

        Returns
        -------
        Cg : spmatrix
            Generator connectivity matrix.
        """
        system = self.system

        # common variables
        nb = system.Bus.n
        ng = system.StaticGen.n

        # bus indices: idx -> uid
        idx_gen = system.StaticGen.get_idx()
        u_gen = system.StaticGen.get(src='u', attr='v', idx=idx_gen)
        on_gen = np.flatnonzero(u_gen)  # uid of online generators
        on_gen_idx = [idx_gen[i] for i in on_gen]  # idx of online generators
        on_gen_bus = system.StaticGen.get(src='bus', attr='v', idx=on_gen_idx)

        row = np.array([system.Bus.idx2uid(x) for x in on_gen_bus])
        col = np.array([idx_gen.index(x) for x in on_gen_idx])
        self.Cg._v = c_sparse((np.ones(len(on_gen_idx)), (row, col)), (nb, ng))
        return self.Cg._v

    def build_cl(self):
        """
        Build load connectivity matrix Cl, and store it in the MParam `Cl`.

        Returns
        -------
        Cl : spmatrix
            Load connectivity matrix.
        """
        system = self.system

        # common variables
        nb = system.Bus.n
        npq = system.PQ.n

        # load indices: idx -> uid
        idx_load = system.PQ.idx.v
        u_load = system.PQ.get(src='u', attr='v', idx=idx_load)
        on_load = np.flatnonzero(u_load)
        on_load_idx = [idx_load[i] for i in on_load]
        on_load_bus = system.PQ.get(src='bus', attr='v', idx=on_load_idx)

        row = np.array([system.Bus.idx2uid(x) for x in on_load_bus])
        col = np.array([system.PQ.idx2uid(x) for x in on_load_idx])
        self.Cl._v = c_sparse((np.ones(len(on_load_idx)), (row, col)), (nb, npq))
        return self.Cl._v

    def build_csh(self):
        """
        Build shunt connectivity matrix Csh, and store it in the MParam `Csh`.

        Returns
        -------
        Csh : spmatrix
            Shunt connectivity matrix.
        """
        system = self.system

        # common variables
        nb = system.Bus.n
        nsh = system.Shunt.n

        # shunt indices: idx -> uid
        idx_shunt = system.Shunt.idx.v
        u_shunt = system.Shunt.get(src='u', attr='v', idx=idx_shunt)
        on_shunt = np.flatnonzero(u_shunt)
        on_shunt_idx = [idx_shunt[i] for i in on_shunt]
        on_shunt_bus = system.Shunt.get(src='bus', attr='v', idx=on_shunt_idx)

        row = np.array([system.Bus.idx2uid(x) for x in on_shunt_bus])
        col = np.array([system.Shunt.idx2uid(x) for x in on_shunt_idx])
        self.Csh._v = c_sparse((np.ones(len(on_shunt_idx)), (row, col)), (nb, nsh))
        return self.Csh._v

    def build_cft(self):
        """
        Build line connectivity matrix Cft and its transpose CftT.
        The Cft and CftT are stored in the MParam `Cft` and `CftT`, respectively.

        Returns
        -------
        Cft : spmatrix
            Line connectivity matrix.
        """
        system = self.system

        # common variables
        nb = system.Bus.n
        nl = system.Line.n

        # line indices: idx -> uid
        idx_line = system.Line.idx.v
        u_line = system.Line.get(src='u', attr='v', idx=idx_line)
        on_line = np.flatnonzero(u_line)
        on_line_idx = [idx_line[i] for i in on_line]
        on_line_bus1 = system.Line.get(src='bus1', attr='v', idx=on_line_idx)
        on_line_bus2 = system.Line.get(src='bus2', attr='v', idx=on_line_idx)

        data_line = np.ones(2*len(on_line_idx))
        data_line[len(on_line_idx):] = -1
        row_line = np.array([system.Bus.idx2uid(x) for x in on_line_bus1 + on_line_bus2])
        col_line = np.array([system.Line.idx2uid(x) for x in on_line_idx + on_line_idx])
        self.Cft._v = c_sparse((data_line, (row_line, col_line)), (nb, nl))
        self.CftT._v = self.Cft._v.T
        return self.Cft._v
