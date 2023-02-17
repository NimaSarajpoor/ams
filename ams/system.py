"""
System class for power system data, methods, and routines.
"""
import configparser
import importlib
import inspect
import logging
from collections import OrderedDict
from typing import Dict, Optional, Tuple, Union

from andes.core import Config
from andes.system import ExistingModels as andes_ExistingModels
from andes.system import System as andes_System
from andes.system import (_config_numpy, load_config_rc)
from ams.models.group import GroupBase
from andes.variables import FileMan

from andes.utils.misc import elapsed

from ams.utils.paths import (ams_root, get_config_path)
import ams.io
from ams.models import file_classes

logger = logging.getLogger(__name__)


def disable_method(func):
    def wrapper(*args, **kwargs):
        logger.warning(f"Method `{func.__name__}` is bounded to ANDES System but not supported in AMS System.")
        return None
    return wrapper


def disable_methods(methods):
    for method in methods:
        setattr(System, method, disable_method(getattr(System, method)))


class ExistingModels(andes_ExistingModels):
    """
    Storage class for existing models used in dispatch routines.

    The class is revised from ``andes.system.ExistingModels``.
    """

    def __init__(self):
        self.dcpflow = OrderedDict()
        self.dcopf = OrderedDict()
        self.pflow = OrderedDict()
        self.opf = OrderedDict()

# TODO: might need a method ``add`` to register a new routine


class System(andes_System):
    """
    System contains data, models, and routines for dispatch modeling and analysis.
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
        # Disable methods not supported in AMS
        # TODO: some of the methods might be used in the future
        func_to_disable = [
            # --- not sure ---
            'set_config', 'set_dae_names', 'set_output_subidx', 'set_var_arrays',
            # --- not used in AMS ---
            '_check_group_common', '_clear_adder_setter', '_e_to_dae', '_expand_pycode', '_finalize_pycode',
            '_find_stale_models', '_get_models', '_init_numba', '_load_calls', '_mp_prepare',
            '_p_restore', '_store_calls', '_store_tf', '_to_orddct', '_v_to_dae',
            'save_config', 'collect_config', 'collect_ref', 'e_clear', 'f_update',
            'fg_to_dae', 'from_ipysheet', 'g_islands', 'g_update', 'get_z',
            'import_routines', 'init', 'j_islands', 'j_update', 'l_update_eq', 'connectivity', 'summary',
            'l_update_var', 'link_ext_param', 'precompile', 'prepare', 'reload', 'remove_pycapsule', 'reset',
            's_update_post', 's_update_var', 'store_adder_setter', 'store_no_check_init',
            'store_sparse_pattern', 'store_switch_times', 'supported_models', 'switch_action', 'to_ipysheet',
            'undill']
        disable_methods(func_to_disable)
        # TODO: it seems that ``connectivity`` and ``summary`` can be skipped in AMS

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
        # TODO: there should be an exit_code for each routine
        self.exit_code = 0                   # command-line exit code, 0 - normal, others - error.
        # ams.io.parse(self)

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

        self.exist = ExistingModels()

        # TODO: revise the following attributes
        self._getters = dict(f=list(), g=list(), x=list(), y=list())
        self._adders = dict(f=list(), g=list(), x=list(), y=list())
        self._setters = dict(f=list(), g=list(), x=list(), y=list())

        self.files = FileMan(case=case, **self.options)    # file path manager

        # internal flags
        self.is_setup = False        # if system has been setup

        # TODO: DEBUG now
        self.import_groups()
        self.import_models()

        func_to_revise = ['set_address', 'vars_to_dae', 'vars_to_models']
        # TODO: ``set_address``: exclude state variables
        # TODO: ``vars_to_dae``: switch from dae to ie
        # TODO: ``vars_to_models``: switch from dae to ie

    def import_groups(self):
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
        ``system.Bus`` stores the `Bus` object, and ``system.GENCLS`` stores the classical
        generator object,

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

        self._list2array()     # `list2array` must come before `link_ext_param`

        self.find_devices()    # find or add required devices

        # === no device addition or removal after this point ===
        # TODO: double check calc_pu_coeff
        self.calc_pu_coeff()   # calculate parameters in system per units
        self.store_existing()  # store models with routine flags

        # assign address at the end before adding devices and processing parameters
        # TODO: enable these functions later on
        # self.set_address(self.exist.pflow)
        # self.set_dae_names(self.exist.pflow)        # needs perf. optimization
        # self.store_sparse_pattern(self.exist.pflow)
        # self.store_adder_setter(self.exist.pflow)

        if ret is True:
            self.is_setup = True  # set `is_setup` if no error occurred
        else:
            logger.error("System setup failed. Please resolve the reported issue(s).")
            self.exit_code += 1

        _, s = elapsed(t0)
        logger.info('System internal structure set up in %s.', s)

        return ret
