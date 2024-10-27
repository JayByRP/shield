from typing import Optional
from fastapi import FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from discord import app_commands, Intents, Client, Embed, Color
from pydantic import BaseModel, HttpUrl
import uvicorn
import asyncio
import re
from websockets import serve, ConnectionClosed
import json
import os
from sqlalchemy.exc import IntegrityError
from database import Base, engine, SessionLocal, test_db_connection
from models import DBCharacter
from dotenv import load_dotenv
import logging

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
        print(f"Error in create_character: {e}")

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
        print(f"Error in edit_character: {e}")

@tree.command(name="delete_character", description="Deletes a character")
async def delete_character(interaction, name: str, password: str):
    try:
        if not verify_character(name, password):
            await interaction.response.send_message("❌ Invalid character name or password.", ephemeral=True)
            return

        db = SessionLocal()
        try:
            character = db.query(DBCharacter).filter(DBCharacter.name == name).first()
            db.delete(character)
            db.commit()
            await interaction.response.send_message(f"✓ Character '{name}' has been deleted!")
            await broadcast_message({'action': 'delete', 'name': name})
        finally:
            db.close()
    except Exception as e:
        await interaction.response.send_message("❌ An error occurred while processing your request.", ephemeral=True)
        print(f"Error in delete_character: {e}")

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
                description=f"[Character Sheet]({character.bio})" if character.bio.startswith('http') else character.bio,
                color=Color.from_str("#fffdd0")
            )
            embed.set_image(url=character.image)
            embed.set_footer(text=character.faceclaim)
            await interaction.response.send_message(embed=embed)
        finally:
            db.close()
    except Exception as e:
        await interaction.response.send_message("❌ An error occurred while processing your request.", ephemeral=True)
        print(f"Error in show_character: {e}")

@tree.command(name="list_all_characters", description="Shows the list of all characters")
async def list_all_characters(interaction):
    try:
        website_url = "https://shield-database.onrender.com/"
        await interaction.response.send_message(f"📚 View the complete character list [here]({website_url})")
    except Exception as e:
        await interaction.response.send_message("❌ An error occurred while processing your request.", ephemeral=True)
        print(f"Error in list_all_characters: {e}")

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
        websocket.is_alive = True  # Track if the connection is alive

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
                if not ws.is_alive:  # Check if the connection is still alive
                    await ws.close()
                else:
                    ws.is_alive = False
                    await ws.ping()  # Send a ping to the client
        await asyncio.sleep(30)  # Wait 30 seconds before the next ping

@client.event
async def on_ready():
    print(f"✓ Bot logged in as {client.user}")
    try:
        await tree.sync()
        print("✓ Command tree synced successfully.")
    except Exception as e:
        print(f"❌ Failed to sync command tree: {e}")

async def start_websocket_server():
    async with serve(websocket_handler, "0.0.0.0", 8765):
        await asyncio.Future()  # run forever

logging.basicConfig(level=logging.INFO)

async def start_discord_bot():
    try:
        logging.info("Connecting to Discord...")
        await client.start(os.getenv('DISCORD_TOKEN'))  # Make sure the token is valid
    except Exception as e:
        logging.error(f"Failed to connect to Discord: {e}")

async def start_fastapi():
    config = uvicorn.Config(app, host="0.0.0.0", port=8000, log_level="info")
    server = uvicorn.Server(config)
    await server.serve()

async def main():
    await asyncio.gather(
        start_websocket_server(),
        start_discord_bot(),
        start_fastapi(),
        ping_websocket_clients()
    )

if __name__ == "__main__":
    asyncio.run(main())
