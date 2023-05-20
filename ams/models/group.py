import logging
import importlib
import inspect
import copy
from collections import OrderedDict

import numpy as np

from andes.models.group import GroupBase as andes_GroupBase

from ams.core.var import Algeb

logger = logging.getLogger(__name__)


class GroupBase(andes_GroupBase):
    """
    Base class for groups.

    Add common_vars and common_params to the group class.

    This class is revised from ``andes.models.group.GroupBase``.
    """
    pass

    def __init__(self):
        super().__init__()
        self.common_params.extend(('idx',))

    # TODO: revise ``set`` method to make sure Group.params are
    # also updated if model.params are updated

    @property
    def idx(self):
        """
        Value of the group idx.
        """
        return self.get_idx()

    def get_idx(self):
        """
        Return the value of group idx.
        """
        all = [mdl.idx.v for _, mdl in self.models.items()]
        flat_list = sorted([val for sublist in all for val in sublist])
        return flat_list

class Undefined(GroupBase):
    """
    The undefined group. Holds models with no ``group``.
    """
    pass


class ACTopology(GroupBase):
    def __init__(self):
        super().__init__()
        self.common_vars.extend(('a', 'v'))


class Cost(GroupBase):
    def __init__(self):
        super().__init__()
        self.common_params.extend(('gen',))


class Collection(GroupBase):
    """Collection of topology models"""
    pass


class StaticGen(GroupBase):
    """
    Generator group.

    Notes
    -----
    For co-simulation with ANDES, replacing static generators with dynamic generators can be found in
    [ANDES StaticGen](https://docs.andes.app/en/latest/groupdoc/StaticGen.html#staticgen).
    """

    def __init__(self):
        super().__init__()
        self.common_params.extend(('Sn', 'Vn', 'p0', 'q0', 'ra', 'xs', 'subidx'))
        self.common_vars.extend(('p', 'q'))


class ACLine(GroupBase):
    def __init__(self):
        super(ACLine, self).__init__()
        self.common_params.extend(('bus1', 'bus2', 'r', 'x'))


class StaticLoad(GroupBase):
    """
    Static load group.
    """
    pass


class StaticShunt(GroupBase):
    """
    Static shunt compensator group.
    """
    pass


class DynLoad(GroupBase):
    """
    Dynamic load group.
    """
    pass


class Information(GroupBase):
    """
    Group for information container models.
    """

    def __init__(self):
        GroupBase.__init__(self)
        self.common_params = []
