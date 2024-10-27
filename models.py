from sqlalchemy import Column, String, Text
from database import Base

class DBCharacter(Base):
    __tablename__ = "characters"
    
    name = Column(String, primary_key=True, index=True)
    faceclaim = Column(String, nullable=False)
    image = Column(String, nullable=False)
    bio = Column(Text, nullable=False)
    password = Column(String, nullable=False)