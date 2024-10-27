from sqlalchemy import Column, String, Text, Enum
from database import Base
import enum

# Define Gender Enum
class GenderEnum(enum.Enum):
    male = "male"
    female = "female"
    non_binary = "non-binary"
    other = "other"

# Define Sexuality Enum
class SexualityEnum(enum.Enum):
    heterosexual = "heterosexual"
    homosexual = "homosexual"
    bisexual = "bisexual"
    pansexual = "pansexual"
    asexual = "asexual"
    other = "other"

class DBCharacter(Base):
    __tablename__ = "characters"
    
    name = Column(String, primary_key=True, index=True)
    faceclaim = Column(String, nullable=False)
    image = Column(String, nullable=False)
    bio = Column(Text, nullable=False)
    password = Column(String, nullable=False)
    gender = Column(Enum(GenderEnum), nullable=True)
    sexuality = Column(Enum(SexualityEnum), nullable=True)