from pydantic import BaseModel
from typing import List


class Station(BaseModel):
    id: int
    type_st: int
    battery_life: float
    latitude: float
    longitude: float
    PM_2_5: float
    PM_10: float
    overTLV: int
    
class StationInput(BaseModel):
    id: int
    latitude: float
    longitude: float
    battery: float

class PlotRequest(BaseModel):
    stations: List[StationInput]
