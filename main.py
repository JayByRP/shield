import os
import asyncio
import logging
from typing import Optional
from fastapi import FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from discord import app_commands, Intents, Client, Embed, Color, Interaction
from discord.app_commands import Choice
from pydantic import BaseModel, HttpUrl
from websockets import serve
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from database import Base, engine, SessionLocal
from models import DBCharacter, GenderEnum, SexualityEnum, YearEnum, ProgramEnum
from dotenv import load_dotenv
import json
import re
import enum

# Load environment variables
load_dotenv()
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD")

# Initialize logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize Discord bot
intents = Intents.default()
intents.message_content = True
client = Client(intents=intents)
tree = app_commands.CommandTree(client)

# Initialize database
Base.metadata.create_all(bind=engine)

# Global websocket connections
websocket_connections = set()

# Pydantic model for request validation
class Character(BaseModel):
    name: str
    faceclaim: str
    image: HttpUrl
    bio: str
    password: str

# Helper functions
def verify_character(name: str, password: str) -> bool:
    db = SessionLocal()
    try:
        character = db.query(DBCharacter).filter(DBCharacter.name == name).first()
        return character and (character.password == password or password == ADMIN_PASSWORD)
    finally:
        db.close()

def is_valid_image_url(url: str) -> bool:
    if not url:
        return False
    pattern = re.compile(r'^https://.*\.(jpg|jpeg|png)$', re.IGNORECASE)
    return bool(pattern.match(url)) and len(url) <= 2048

async def broadcast_message(message: dict):
    if not websocket_connections:
        return
    websocket_message = json.dumps(message)
    await asyncio.gather(*[ws.send(websocket_message) for ws in websocket_connections])

# Autocomplete functions
async def character_name_autocomplete(interaction: Interaction, current: str):
    db = SessionLocal()
    try:
        characters = db.query(DBCharacter).filter(DBCharacter.name.ilike(f"{current}%")).all()
        return [Choice(name=character.name, value=character.name) for character in characters[:5]]
    finally:
        db.close()
async def gender_autocomplete(interaction: Interaction, current: str):
    return [Choice(name=gender.value, value=gender.value) for gender in GenderEnum if gender.value.lower().startswith(current.lower())]

async def sexuality_autocomplete(interaction: Interaction, current: str):
    return [Choice(name=sexuality.value, value=sexuality.value) for sexuality in SexualityEnum if sexuality.value.lower().startswith(current.lower())]

async def program_autocomplete(interaction: Interaction, current: str):
    return [Choice(name=program.value, value=program.value) for program in ProgramEnum if program.value.lower().startswith(current.lower())]

async def year_autocomplete(interaction: Interaction, current: str):
    return [Choice(name=year.value, value=year.value) for year in YearEnum if year.value.lower().startswith(current.lower())]

@tree.command(name="create_character", description="Creates a new character profile")
@app_commands.autocomplete(gender=gender_autocomplete, sexuality=sexuality_autocomplete, program=program_autocomplete, year=year_autocomplete)
async def create_character(
    interaction: Interaction, 
    name: str, 
    faceclaim: str, 
    image: str, 
    bio: str, 
    password: str, 
    gender: str, 
    sexuality: str, 
    program: str,
    year: str
):
    try:
        if not is_valid_image_url(image):
            await interaction.response.send_message("❌ Invalid image URL. Please provide an HTTPS URL ending with .jpg, .jpeg, or .png.", ephemeral=True)
            return

        db = SessionLocal()
        try:
            character = DBCharacter(
                name=name,
                faceclaim=faceclaim,
                image=image,
                bio=bio,
                password=password,
                gender=GenderEnum(gender),
                sexuality=SexualityEnum(sexuality),
                program=ProgramEnum(program),
                year=YearEnum(year)
            )
            db.add(character)
            db.commit()
            await interaction.response.send_message(f"✓ Character '{name}' has been created successfully!")
            await broadcast_message({
                'action': 'create',
                'name': name,
                'faceclaim': faceclaim,
                'image': image,
                'bio': bio,
                'gender': gender,
                'sexuality': sexuality,
                'program': program,
                'year': year
            })
        except IntegrityError:
            await interaction.response.send_message(f"❌ A character named '{name}' already exists!", ephemeral=True)
        finally:
            db.close()
    except Exception as e:
        await interaction.response.send_message("❌ An error occurred while processing your request.", ephemeral=True)
        logging.error(f"Error in create_character: {e}")

@tree.command(name="edit_character", description="Edits an existing character")
@app_commands.autocomplete(name=character_name_autocomplete, gender=gender_autocomplete, sexuality=sexuality_autocomplete, program=program_autocomplete, year=year_autocomplete)
async def edit_character(
    interaction: Interaction, 
    name: str, 
    password: str, 
    faceclaim: Optional[str] = None, 
    image: Optional[str] = None, 
    bio: Optional[str] = None, 
    gender: Optional[str] = None, 
    sexuality: Optional[str] = None,
    program: Optional[str] = None,
    year: Optional[str] = None
):
    try:
        if not verify_character(name, password):
            await interaction.response.send_message("❌ Invalid character name or password.", ephemeral=True)
            return

        if image and not is_valid_image_url(image):
            await interaction.response.send_message("❌ Invalid image URL.", ephemeral=True)
            return

        db = SessionLocal()
        try:
            character = db.query(DBCharacter).filter(DBCharacter.name == name).first()
            if not character:
                await interaction.response.send_message("❌ Character not found.", ephemeral=True)
                return
            
            if faceclaim:
                character.faceclaim = faceclaim
            if image:
                character.image = image
            if bio:
                character.bio = bio
            if gender:
                character.gender = GenderEnum(gender)
            if sexuality:
                character.sexuality = SexualityEnum(sexuality)
            if program:
                character.program = ProgramEnum(program)
            if year:
                character.year = YearEnum(year)
            db.commit()
            await interaction.response.send_message(f"✓ Character '{name}' has been updated!")
            await broadcast_message({'action': 'edit', 'name': name})
        finally:
            db.close()
    except Exception as e:
        await interaction.response.send_message("❌ An error occurred while processing your request.", ephemeral=True)
        logging.error(f"Error in edit_character: {e}")

@tree.command(name="delete_character", description="Deletes a character")
@app_commands.autocomplete(name=character_name_autocomplete)
async def delete_character(interaction: Interaction, name: str, password: str):
    try:
        if not verify_character(name, password):
            await interaction.response.send_message("❌ Invalid character name or password.", ephemeral=True)
            return

        db = SessionLocal()
        try:
            character = db.query(DBCharacter).filter(DBCharacter.name == name).first()
            if not character:
                await interaction.response.send_message("❌ Character not found.", ephemeral=True)
                return

            db.delete(character)
            db.commit()
            await interaction.response.send_message(f"✓ Character '{name}' has been deleted!")
            await broadcast_message({'action': 'delete', 'name': name})
        finally:
            db.close()
    except Exception as e:
        await interaction.response.send_message("❌ An error occurred while processing your request.", ephemeral=True)
        logging.error(f"Error in delete_character: {e}")

@tree.command(name="show_character", description="Shows a character's profile")
@app_commands.autocomplete(name=character_name_autocomplete)
async def show_character(interaction: Interaction, name: str):
    try:
        db = SessionLocal()
        try:
            character = db.query(DBCharacter).filter(DBCharacter.name == name).first()
            if not character:
                await interaction.response.send_message("❌ Character not found.", ephemeral=True)
                return
            embed = Embed(
                title=character.name.upper(),
                description=f"[Character Sheet]({character.bio})" if character.bio.startswith('http') else "N/A",
                color=Color.from_str("#fffdd0")
            )
            embed.set_image(url=character.image)
            embed.set_footer(text=character.faceclaim)
            await interaction.response.send_message(embed=embed)
        finally:
            db.close()
    except Exception as e:
        await interaction.response.send_message("❌ An error occurred while processing your request.", ephemeral=True)
        logging.error(f"Error in show_character: {e}")

@tree.command(name="character_list", description="Shows the list of all characters")
async def list_all_characters(interaction):
    try:
        website_url = "https://shield-hzo0.onrender.com/"
        await interaction.response.send_message(f"📚 View the complete character list [here]({website_url})")
    except Exception as e:
        await interaction.response.send_message("❌ An error occurred while processing your request.", ephemeral=True)
        logging.error(f"Error in list_all_characters: {e}")

# FastAPI Setup
app = FastAPI()

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount static files
app.mount("/static", StaticFiles(directory="public"), name="static")

# API endpoints
@app.get("/")
async def root():
    return FileResponse("public/index.html")

@app.get("/api/characters")
async def get_characters():
    try:
        db = SessionLocal()
        try:
            characters = db.query(DBCharacter).all()
            return [
                {
                    "name": c.name,
                    "faceclaim": c.faceclaim,
                    "image": c.image,
                    "bio": c.bio,
                    "gender": c.gender.value,
                    "sexuality": c.sexuality.value,
                    "program": c.program.value,
                    "year": c.year.value
                } 
                for c in characters
            ]
        finally:
            db.close()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/health")
async def health_check():
    return {"status": "healthy"}

# WebSocket handling
async def websocket_handler(websocket):
    try:
        websocket_connections.add(websocket)
        async for _ in websocket:
            pass
    finally:
        websocket_connections.remove(websocket)

async def ping_websocket_clients():
    while True:
        if websocket_connections:
            for ws in list(websocket_connections):
                try:
                    await ws.ping()
                except Exception:
                    websocket_connections.remove(ws)
        await asyncio.sleep(30)

@client.event
async def on_ready():
    logging.info(f'Logged in as {client.user}')
    await tree.sync()

async def start_discord_bot():
    await client.start(os.getenv("DISCORD_TOKEN"))

async def websocket_server():
    async with serve(websocket_handler, "0.0.0.0", 6789):
        await ping_websocket_clients()

def upgrade_database():
    db = SessionLocal()
    try:
        with db.begin():
            db.execute(text("""
                ALTER TABLE characters 
                ADD COLUMN gender VARCHAR(255);
            """))
            db.execute(text("""
                ALTER TABLE characters 
                ADD COLUMN sexuality VARCHAR(255);
            """))
            db.execute(text("""
                ALTER TABLE characters 
                ADD COLUMN program VARCHAR(255);
            """))
            db.execute(text("""
                ALTER TABLE characters 
                ADD COLUMN year VARCHAR(255);
            """))
        logger.info("Database upgraded successfully: program and year columns added.")
    except Exception as e:
        logger.error(f"Database upgrade failed: {e}")
    finally:
        db.close()

# Lifespan
@app.on_event("startup")
async def startup_event():
    upgrade_database()
    asyncio.create_task(start_discord_bot())
    asyncio.create_task(websocket_server())

@app.on_event("shutdown")
async def shutdown_event():
    if client.is_ready():
        await client.close()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "8000")))