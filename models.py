from sqlalchemy import Column, String, Text, Enum
from database import Base
import enum

# Define Program Enum
class ProgramEnum(enum.Enum):
    operations = "Operations"
    intelligence = "Intelligence"
    sci-tech = "Sci-Tech"

# Define Year Enum
class YearEnum(enum.Enum):
    first = "1st Year"
    second = "2nd Year"
    third = "3rd Year"
    fourth = "4th Year"

# Define Gender Enum
class GenderEnum(enum.Enum):
    male = "Male"
    female = "Female"
    non_binary = "Non-binary"
    other = "Other"

# Define Sexuality Enum
class SexualityEnum(enum.Enum):
    heterosexual = "Heterosexual"
    homosexual = "Homosexual"
    bisexual = "Bisexual"
    pansexual = "Pansexual"
    asexual = "Asexual"
    other = "Other"

class DBCharacter(Base):
    __tablename__ = "characters"
    
    name = Column(String, primary_key=True, index=True)
    faceclaim = Column(String, nullable=False)
    image = Column(String, nullable=False)
    bio = Column(Text, nullable=False)
    password = Column(String, nullable=False)
    gender = Column(Enum(GenderEnum), nullable=True)
    sexuality = Column(Enum(SexualityEnum), nullable=True)
    program = Column(Enum(ProgramEnum), nullable=True)
    year = Column(Enum(YearEnum), nullable=True)