import logging

from andes.core.model import ModelData
from andes.utils.tab import Tab
from ams.core.model import Model

logger = logging.getLogger(__name__)

class ZoneData(ModelData):
    def __init__(self):
        super().__init__()


class Zone(ZoneData, Model):
    """
    Area model.
    """
    def __init__(self, system, config):
        ZoneData.__init__(self)
        Model.__init__(self, system, config)

        self.group = 'Collection'

    def bus_table(self):
        """
        Return a formatted table with area idx and bus idx correspondence

        Returns
        -------
        str
            Formatted table

        """
        if self.n:
            header = ['Zone ID', 'Bus ID']
            rows = [(i, j) for i, j in zip(self.idx.v, self.Bus.v)]
            return Tab(header=header, data=rows).draw()
        else:
            return ''
