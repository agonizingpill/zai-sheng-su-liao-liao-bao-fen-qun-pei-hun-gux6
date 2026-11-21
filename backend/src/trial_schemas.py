from pydantic import BaseModel,Field,ConfigDict
class Clean(BaseModel):model_config=ConfigDict(str_strip_whitespace=True)
class TrialIn(Clean):
    code:str=Field(min_length=2,max_length=70)
class PairIn(Clean):
    blend_a_id:int=Field(gt=0)
    blend_b_id:int=Field(gt=0)
    dryer_id:int=Field(gt=0)
class FirstTestIn(Clean):
    blend_id:int=Field(gt=0)
