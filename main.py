import os
import asyncio
import logging
import uvicorn
from typing import Optional
from fastapi import FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from discord import app_commands, Intents, Client, Embed, Color
from pydantic import BaseModel, HttpUrl
from websockets import serve, ConnectionClosed
from sqlalchemy.exc import IntegrityError
from database import Base, engine, SessionLocal
from models import DBCharacter
from dotenv import load_dotenv
import json
import re

# Load environment variables
load_dotenv()
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD")

# Initialize FastAPI
app = FastAPI()
app.mount("/static", StaticFiles(directory="public"), name="static")

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

# Discord bot commands
@tree.command(name="create_character", description="Creates a new character profile")
async def create_character(interaction, name: str, faceclaim: str, image: str, bio: str, password: str):
    try:
        if not is_valid_image_url(image):
            await interaction.response.send_message("❌ Invalid image URL. Please provide an HTTPS URL ending with .jpg, .jpeg, or .png.", ephemeral=True)
            return

        db = SessionLocal()
        try:
            character = DBCharacter(name=name, faceclaim=faceclaim, image=image, bio=bio, password=password)
            db.add(character)
            db.commit()
            await interaction.response.send_message(f"✓ Character '{name}' has been created successfully!")
            await broadcast_message({'action': 'create', 'name': name, 'faceclaim': faceclaim, 'image': image, 'bio': bio})
        except IntegrityError:
            await interaction.response.send_message(f"❌ A character named '{name}' already exists!", ephemeral=True)
        finally:
            db.close()
    except Exception as e:
        await interaction.response.send_message("❌ An error occurred while processing your request.", ephemeral=True)
        logging.error(f"Error in create_character: {e}")

@tree.command(name="edit_character", description="Edits an existing character")
async def edit_character(interaction, name: str, password: str, faceclaim: Optional[str] = None, image: Optional[str] = None, bio: Optional[str] = None):
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
            db.commit()
            await interaction.response.send_message(f"✓ Character '{name}' has been updated!")
            await broadcast_message({'action': 'edit', 'name': name})
        finally:
            db.close()
    except Exception as e:
        await interaction.response.send_message("❌ An error occurred while processing your request.", ephemeral=True)
        logging.error(f"Error in edit_character: {e}")

@tree.command(name="delete_character", description="Deletes a character")
async def delete_character(interaction, name: str, password: str):
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
async def show_character(interaction, name: str):
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

@tree.command(name="list_all_characters", description="Shows the list of all characters")
async def list_all_characters(interaction):
    try:
        website_url = "https://shield-database.onrender.com/"
        await interaction.response.send_message(f"📚 View the complete character list [here]({website_url})")
    except Exception as e:
        await interaction.response.send_message("❌ An error occurred while processing your request.", ephemeral=True)
        logging.error(f"Error in list_all_characters: {e}")

@app.get("/")
async def read_root(request: Request):
    return FileResponse("public/index.html")

@app.head("/")
async def head_root(request: Request):
    return FileResponse("public/index.html")

@app.get("/api/characters")
async def get_characters():
    try:
        db = SessionLocal()
        try:
            characters = db.query(DBCharacter).all()
            return [{"name": c.name, "faceclaim": c.faceclaim, "image": c.image, "bio": c.bio} for c in characters]
        finally:
            db.close()
    except Exception as e:
        raise HTTPException(status_code=500, detail="Failed to fetch characters")

async def websocket_handler(websocket):
    try:
        websocket_connections.add(websocket)
        async for _ in websocket:  # Keep the connection open
            pass  # Placeholder for receiving messages if needed
    except ConnectionClosed:
        pass
    finally:
        websocket_connections.remove(websocket)

async def ping_websocket_clients():
    while True:
        if websocket_connections:
            for ws in list(websocket_connections):
                try:
                    await ws.ping()  # Send a ping to the client
                except Exception:
                    websocket_connections.remove(ws)  # Remove if the connection fails
        await asyncio.sleep(30)  # Wait 30 seconds before the next ping

@client.event
async def on_ready():
    print(f"✓ Bot logged in as {client.user}")
    try:
        await tree.sync()
        print("✓ Command tree synced successfully.")
    except Exception as e:
        logging.error(f"❌ Failed to sync command tree: {e}")

async def start_websocket_server():
    async with serve(websocket_handler, "0.0.0.0", 8765):
        await asyncio.Future()  # run forever

async def run_discord_bot():
    await client.start(os.getenv("DISCORD_TOKEN"))

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    loop = asyncio.get_event_loop()

    # Start the FastAPI app and websocket server
    loop.create_task(start_websocket_server())

    # Start Discord bot in a separate task
    loop.create_task(run_discord_bot())

    # Run the FastAPI app in the event loop
    uvicorn.run(app, host="0.0.0.0", port=8000)
    client.run(os.getenv("DISCORD_TOKEN"))