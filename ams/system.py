"""
Module for system.
"""
import configparser
import copy
import importlib
import inspect
import logging
from collections import OrderedDict
from typing import Dict, Optional, Tuple, Union

import numpy as np
import sympy as sp

from andes.core import Config
from andes.system import System as andes_System
from andes.system import (_config_numpy, load_config_rc)
from andes.variables import FileMan

from andes.utils.misc import elapsed
from andes.utils.tab import Tab
from andes.shared import pd

from ams.models.group import GroupBase
from ams.routines.type import TypeBase
from ams.models import file_classes
from ams.routines import all_routines
from ams.utils.paths import get_config_path
from ams.core import Algeb
from ams.core.matprocessor import MatProcessor
from ams.interop.andes import to_andes

logger = logging.getLogger(__name__)


def disable_method(func):
    def wrapper(*args, **kwargs):
        logger.warning(f"Method `{func.__name__}` is included in ANDES System but not supported in AMS System.")
        return None
    return wrapper


def disable_methods(methods):
    for method in methods:
        setattr(System, method, disable_method(getattr(System, method)))


class System(andes_System):
    """
    A subclass of ``andes.system.System``, this class encapsulates data, models, 
    and routines for dispatch modeling and analysis in power systems.
    Some methods  inherited from the parent class are intentionally disabled.

    Parameters
    ----------
    case : str, optional
        The path to the case file.
    name : str, optional
        Name of the system instance.
    config : dict, optional
        Configuration options for the system. Overrides the default configuration if provided.
    config_path : str, optional
        The path to the configuration file.
    default_config : bool, optional
        If True, the default configuration file is loaded.
    options : dict, optional
        Additional configuration options for the system.
    **kwargs :
        Additional configuration options passed as keyword arguments.

    Attributes
    ----------
    name : str
        Name of the system instance.
    options : dict
        A dictionary containing configuration options for the system.
    models : OrderedDict
        An ordered dictionary holding the model names and instances.
    model_aliases : OrderedDict
        An ordered dictionary holding model aliases and their corresponding instances.
    groups : OrderedDict
        An ordered dictionary holding group names and instances.
    routines : OrderedDict
        An ordered dictionary holding routine names and instances.
    types : OrderedDict
        An ordered dictionary holding type names and instances.
    mats : MatrixProcessor, None
        A matrix processor instance, initially set to None.
    mat : OrderedDict
        An ordered dictionary holding common matrices.
    exit_code : int
        Command-line exit code. 0 indicates normal execution, while other values indicate errors.
    recent : RecentSolvedRoutines, None
        An object storing recently solved routines, initially set to None.
    dyn : ANDES System, None
        linked dynamic system, initially set to None.
        It is an instance of the ANDES system, which will be automatically
        set when using ``System.to_andes()``.
    files : FileMan
        File path manager instance.
    is_setup : bool
        Internal flag indicating if the system has been set up.

    Methods
    -------
    setup:
        Set up the system.
    to_andes:
        Convert the system to an ANDES system.
    """

    def __init__(self,
                 case: Optional[str] = None,
                 name: Optional[str] = None,
                 config: Optional[Dict] = None,
                 config_path: Optional[str] = None,
                 default_config: Optional[bool] = False,
                 options: Optional[Dict] = None,
                 **kwargs
                 ):

        # TODO: might need _check_group_common
        func_to_disable = [
            # --- not sure ---
            'set_config', 'set_dae_names', 'set_output_subidx', 'set_var_arrays',
            # --- not used in AMS ---
            '_check_group_common', '_clear_adder_setter', '_e_to_dae', '_expand_pycode', '_finalize_pycode',
            '_find_stale_models', '_get_models', '_init_numba', '_load_calls', '_mp_prepare',
            '_p_restore', '_store_calls', '_store_tf', '_to_orddct', '_v_to_dae',
            'save_config', 'collect_config', 'e_clear', 'f_update',
            'fg_to_dae', 'from_ipysheet', 'g_islands', 'g_update', 'get_z',
            'init', 'j_islands', 'j_update', 'l_update_eq', 'connectivity', 'summary',
            'l_update_var', 'precompile', 'prepare', 'reload', 'remove_pycapsule', 'reset',
            's_update_post', 's_update_var', 'store_adder_setter', 'store_no_check_init',
            'store_sparse_pattern', 'store_switch_times', 'switch_action', 'to_ipysheet',
            'undill']
        disable_methods(func_to_disable)

        self.name = name
        self.options = {}
        if options is not None:
            self.options.update(options)
        if kwargs:
            self.options.update(kwargs)
        self.models = OrderedDict()          # model names and instances
        self.model_aliases = OrderedDict()   # alias: model instance
        self.groups = OrderedDict()          # group names and instances
        self.routines = OrderedDict()        # routine names and instances
        self.types = OrderedDict()           # type names and instances
        self.mats = None                     # matrix processor
        self.mat = OrderedDict()             # common matrices
        # TODO: there should be an exit_code for each routine
        self.exit_code = 0                   # command-line exit code, 0 - normal, others - error.
        self.recent = None                   # recent solved routines
        self.dyn = None                      # ANDES system

        # get and load default config file
        self._config_path = get_config_path()
        if config_path is not None:
            self._config_path = config_path
        if default_config is True:
            self._config_path = None

        self._config_object = load_config_rc(self._config_path)
        self._update_config_object()
        self.config = Config(self.__class__.__name__, dct=config)
        self.config.load(self._config_object)

        # custom configuration for system goes after this line
        self.config.add(OrderedDict((('freq', 60),
                                     ('mva', 100),
                                     ('seed', 'None'),
                                     ('save_stats', 0),  # TODO: not sure what this is for
                                     ('np_divide', 'warn'),
                                     ('np_invalid', 'warn'),
                                     )))

        self.config.add_extra("_help",
                              freq='base frequency [Hz]',
                              mva='system base MVA',
                              seed='seed (or None) for random number generator',
                              np_divide='treatment for division by zero',
                              np_invalid='treatment for invalid floating-point ops.',
                              )

        self.config.add_extra("_alt",
                              freq="float",
                              mva="float",
                              seed='int or None',
                              np_divide={'ignore', 'warn', 'raise', 'call', 'print', 'log'},
                              np_invalid={'ignore', 'warn', 'raise', 'call', 'print', 'log'},
                              )

        self.config.check()
        _config_numpy(seed=self.config.seed,
                      divide=self.config.np_divide,
                      invalid=self.config.np_invalid,
                      )

        # TODO: revise the following attributes, it seems that these are not used in AMS
        self._getters = dict(f=list(), g=list(), x=list(), y=list())
        self._adders = dict(f=list(), g=list(), x=list(), y=list())
        self._setters = dict(f=list(), g=list(), x=list(), y=list())

        self.files = FileMan(case=case, **self.options)    # file path manager

        # internal flags
        self.is_setup = False        # if system has been setup

        self.import_types()
        self.import_groups()
        self.import_models()
        self.import_routines()

    def import_types(self):
        """
        Import all types classes defined in ``routines/type.py``.

        Types will be stored as instances with the name as class names.
        All types will be stored to dictionary ``System.types``.
        """
        module = importlib.import_module('ams.routines.type')
        for m in inspect.getmembers(module, inspect.isclass):
            name, cls = m
            if name == 'TypeBase':
                continue
            elif not issubclass(cls, TypeBase):
                # skip other imported classes such as `OrderedDict`
                continue

            self.__dict__[name] = cls()
            self.types[name] = self.__dict__[name]

    def _collect_group_data(self, items):
        """
        Set the owner for routine attributes: ``RParam``, ``Var``, and ``RBaseService``.
        """
        for item_name, item in items.items():
            if item.model in self.groups.keys():
                item.is_group = True
                item.owner = self.groups[item.model]
            elif item.model in self.models.keys():
                item.owner = self.models[item.model]
            elif item_name in ['PTDF1', 'PTDF2', 'pd1', 'pd2']:
                pass
            else:
                msg = f'Model indicator \'{item.model}\' of <{item.rtn.class_name}.{item_name}>'
                msg += f' is not a model or group. Likely a modeling error.'
                logger.warning(msg)

    def import_routines(self):
        """
        Import routines as defined in ``routines/__init__.py``.

        Routines will be stored as instances with the name as class names.
        All routines will be stored to dictionary ``System.routines``.

        Examples
        --------
        ``System.PFlow`` is the power flow routine instance.
        """
        for file, cls_list in all_routines.items():
            for cls_name in cls_list:
                routine = importlib.import_module('ams.routines.' + file)
                the_class = getattr(routine, cls_name)
                attr_name = cls_name
                self.__dict__[attr_name] = the_class(system=self, config=self._config_object)
                self.routines[attr_name] = self.__dict__[attr_name]
                self.routines[attr_name].config.check()
                # NOTE: the following code is not used in ANDES
                for vname, rtn in self.routines.items():
                    # TODO: collect routiens into types
                    type_name = getattr(rtn, 'type')
                    type_instance = self.types[type_name]
                    type_instance.routines[vname] = rtn
                    # self.types[rtn.type].routines[vname] = rtn
                    # Collect rparams
                    rparams = getattr(rtn, 'rparams')
                    self._collect_group_data(rparams)
                    # Collect vars
                    vars = getattr(rtn, 'vars')
                    self._collect_group_data(vars)
                    # Collect services
                    services = getattr(rtn, 'services')
                    self._collect_group_data(services)

    def import_groups(self):
        """
        Import all groups classes defined in ``models/group.py``.

        Groups will be stored as instances with the name as class names.
        All groups will be stored to dictionary ``System.groups``.
        """
        module = importlib.import_module('ams.models.group')
        for m in inspect.getmembers(module, inspect.isclass):

            name, cls = m
            if name == 'GroupBase':
                continue
            elif not issubclass(cls, GroupBase):
                # skip other imported classes such as `OrderedDict`
                continue

            self.__dict__[name] = cls()
            self.groups[name] = self.__dict__[name]

    def import_models(self):
        """
        Import and instantiate models as System member attributes.

        Models defined in ``models/__init__.py`` will be instantiated `sequentially` as attributes with the same
        name as the class name.
        In addition, all models will be stored in dictionary ``System.models`` with model names as
        keys and the corresponding instances as values.

        Examples
        --------
        ``system.Bus`` stores the `Bus` object, and ``system.PV`` stores the PV generator object.

        ``system.models['Bus']`` points the same instance as ``system.Bus``.
        """
        for fname, cls_list in file_classes:
            for model_name in cls_list:
                the_module = importlib.import_module('ams.models.' + fname)
                the_class = getattr(the_module, model_name)
                self.__dict__[model_name] = the_class(system=self, config=self._config_object)
                self.models[model_name] = self.__dict__[model_name]
                self.models[model_name].config.check()

                # link to the group
                group_name = self.__dict__[model_name].group
                self.__dict__[group_name].add_model(model_name, self.__dict__[model_name])
        # NOTE: model_aliases is not used in AMS currently
        # for key, val in ams.models.model_aliases.items():
        #     self.model_aliases[key] = self.models[val]
        #     self.__dict__[key] = self.models[val]

    def collect_ref(self):
        """
        Collect indices into `BackRef` for all models.
        """
        models_and_groups = list(self.models.values()) + list(self.groups.values())

        # create an empty list of lists for all `BackRef` instances
        for model in models_and_groups:
            for ref in model.services_ref.values():
                ref.v = [list() for _ in range(model.n)]

        # `model` is the model who stores `IdxParam`s to other models
        # `BackRef` is declared at other models specified by the `model` parameter
        # of `IdxParam`s.

        for model in models_and_groups:
            if model.n == 0:
                continue

            # skip: a group is not allowed to link to other groups
            if not hasattr(model, "idx_params"):
                continue

            for idxp in model.idx_params.values():
                if (idxp.model not in self.models) and (idxp.model not in self.groups):
                    continue
                dest = self.__dict__[idxp.model]

                if dest.n == 0:
                    continue

                for name in (model.class_name, model.group):
                    # `BackRef` not requested by the linked models or groups
                    if name not in dest.services_ref:
                        continue

                    for model_idx, dest_idx in zip(model.idx.v, idxp.v):
                        if dest_idx not in dest.uid:
                            continue

                        dest.set_backref(name,
                                         from_idx=model_idx,
                                         to_idx=dest_idx)

    def setup(self):
        """
        Set up system for studies.

        This function is to be called after adding all device data.
        """
        ret = True
        t0, _ = elapsed()

        if self.is_setup:
            logger.warning('System has been setup. Calling setup twice is not allowed.')
            ret = False
            return ret

        self.collect_ref()
        self._list2array()     # `list2array` must come before `link_ext_param`
        if not self.link_ext_param():
            ret = False

        if self.Line.rate_a.v.max() == 0:
            logger.warning("Line rate_a is corrected to large value automatically.")
            self.Line.rate_a.v = 99
        # === no device addition or removal after this point ===
        # TODO: double check calc_pu_coeff
        self.calc_pu_coeff()   # calculate parameters in system per units
        # self.store_existing()  # store models with routine flags

        if ret is True:
            self.is_setup = True  # set `is_setup` if no error occurred
        else:
            logger.error("System setup failed. Please resolve the reported issue(s).")
            self.exit_code += 1

        a0 = 0
        for mname, mdl in self.models.items():
            for aname, algeb in mdl.algebs.items():
                algeb.v = np.zeros(algeb.owner.n)
                algeb.a = np.arange(a0, a0 + algeb.owner.n)
                a0 += algeb.owner.n

        # set up common matrix
        self.mats = MatProcessor(self)       # matrix processor

        # FIXME: hard coded here
        # Set nuemerical values for special params
        gen_bus = self.StaticGen.get(src='bus', attr='v',
                                     idx=self.StaticGen.get_idx())
        all_bus = self.Bus.idx.v
        regBus = [int(bus) if isinstance(bus, (int, float)) else bus for bus in gen_bus]
        redBus = [int(bus) if isinstance(bus, (int, float)) else bus for bus in all_bus if bus not in gen_bus]

        # Restrucrue PQ load value to match gen bus pattern
        # FIXME: if we need sparse matrix storage?
        idx_PD1 = self.PQ.find_idx(keys="bus", values=regBus, allow_none=True, default=None)
        idx_PD2 = self.PQ.find_idx(keys="bus", values=redBus, allow_none=True, default=None)
        PD1 = self.PQ.get(src='p0', attr='v', idx=idx_PD1)
        PD1 = np.array(PD1)
        PD2 = self.PQ.get(src='p0', attr='v', idx=idx_PD2)
        PD2 = np.array(PD2)
        PTDF1, PTDF2 = self.mats.rePTDF()

        self.mat = OrderedDict([
            ('pd1', PD1), ('pd2', PD2),
            ('PTDF1', PTDF1), ('PTDF2', PTDF2),
        ])

        # NOTE: initialize om for all routines
        for vname, rtn in self.routines.items():
            # rtn.setup()  # not setup optimization model in system setup stage
            a0 = 0
            for raname, var in rtn.vars.items():
                var.v = np.zeros(var.owner.n)
                var.a = np.arange(a0, a0 + var.owner.n)
                a0 += var.owner.n
            for rpname, rparam in rtn.rparams.items():
                if rpname in self.mat.keys():
                    # NOTE: set numerical values for rparams that are defined in system.mat
                    rparam.is_ext = True
                    rparam._v = self.mat[rpname]
                elif rparam.is_ext is True:
                    # NOTE: register user-defined rparams to system.mat
                    self.mat[rpname] = rparam._v

        _, s = elapsed(t0)
        logger.info('System set up in %s.', s)

        return ret

    # FIXME: remove unused methods
    # # Disable methods not supported in AMS
    # func_to_include = [
    #     'import_models', 'import_groups', 'import_routines',
    #     'setup', 'init_algebs',
    #     '_update_config_object',
    #     ]
    # # disable_methods(func_to_disable)
    # __dict__ = {method: lambda self: self.x for method in func_to_include}

    def supported_routines(self, export='plain'):
        """
        Return the support type names and routine names in a table.

        Returns
        -------
        str
            A table-formatted string for the types and routines
        """

        def rst_ref(name, export):
            """
            Refer to the model in restructuredText mode so that
            it renders as a hyperlink.
            """

            if export == 'rest':
                return ":ref:`" + name + '`'
            else:
                return name

        pairs = list()
        for g in self.types:
            routines = list()
            for m in self.types[g].routines:
                routines.append(rst_ref(m, export))
            if len(routines) > 0:
                pairs.append((rst_ref(g, export), ', '.join(routines)))

        tab = Tab(title='Supported Types and Routines',
                  header=['Type', 'Routines'],
                  data=pairs,
                  export=export,
                  )

        return tab.draw()

    def to_andes(self, setup=True, addfile=None, overwite=None, no_keep=True,
                 **kwargs):
        """
        Convert the AMS system to an ANDES system.
        This function is a wrapper of ``ams.interop.andes.to_andes()``.

        Using the file conversion ``sp.to_andes()`` will automatically
        link the AMS system instance to the converted ANDES system instance
        in the AMS system attribute ``sp.dyn``.

        Parameters
        ----------
        system : System
            The AMS system to be converted to ANDES format.
        setup : bool, optional
            Whether to call `setup()` after the conversion. Default is True.
        addfile : str, optional
            The additional file to be converted to ANDES dynamic mdoels.
        overwrite : bool, optional
            Whether to overwrite the existing file.
        no_keep : bool, optional
            True to remove the converted file after the conversion.
        **kwargs : dict
            Keyword arguments to be passed to `andes.system.System`.

        Returns
        -------
        andes : andes.system.System
            The converted ANDES system.

        Examples
        --------
        >>> import ams
        >>> import andes
        >>> sp = ams.load(ams.get_case('ieee14/ieee14_rted.xlsx'), setup=True)
        >>> sa = sp.to_andes(setup=False,
        ...                  addfile=andes.get_case('ieee14/ieee14_wt3.xlsx'),
        ...                  overwrite=True, no_keep=True, no_output=True)
        """
        return to_andes(self, setup=setup, addfile=addfile,
                        overwite=overwite, no_keep=no_keep,
                        **kwargs)
