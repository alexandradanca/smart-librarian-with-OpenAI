import json
import os
from openai import OpenAI
import chromadb
from chromadb.utils import embedding_functions
import re
from config import (
    OPENAI_API_KEY,
    OPENAI_EMBEDDING_MODEL,
    CHROMA_API_KEY,
    CHROMA_TENANT,
    CHROMA_DATABASE
)

#########################################################
#################### Data Loading #######################
#########################################################
# Path to book summaries JSON and ChromaDB collection name
JSON_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "book_summaries.json")
COLLECTION_NAME = "book_chunks"

# Load book summaries from JSON file
with open(JSON_PATH, "r", encoding="utf-8") as f:
    books = json.load(f)

#########################################################
############### Text Chunking Function ##################
#########################################################
# Split book summary text into sentences
def chunk_text(text):
    # Split text into sentences using period, exclamation mark, or question mark
    sentences = re.split(r'(?<=[.!?]) +', text)
    # Remove empty spaces
    return [s.strip() for s in sentences if s.strip()]

#########################################################
############ Prepare Chunks and Metadata ################
#########################################################
chunks = []
metadatas = []
for book in books:
    # Combine title, summary, and themes into one chunk
    chunk = f"Title: {book['title']}\nSummary: {book['summary']}\nThemes: {', '.join(book['themes'])}"
    chunks.append(chunk)
    metadatas.append({"title": book["title"], "themes": ", ".join(book["themes"])})

#########################################################
############### Embedding Function Setup ################
#########################################################
embedding_fn = embedding_functions.OpenAIEmbeddingFunction(
    api_key=OPENAI_API_KEY,
    model_name=OPENAI_EMBEDDING_MODEL
)

#########################################################
############### ChromaDB Cloud Setup ####################
#########################################################
client = chromadb.CloudClient(
    api_key=os.getenv("CHROMA_API_KEY"),
    tenant=os.getenv("CHROMA_TENANT"),
    database=os.getenv("CHROMA_DATABASE")
)
# Drop and recreate collection to remove old data
client.delete_collection(name=COLLECTION_NAME)
collection = client.get_or_create_collection(
    name=COLLECTION_NAME,
    embedding_function=embedding_fn
)

#########################################################
############### Insert Data into ChromaDB ###############
#########################################################
collection.add(
    documents=chunks,
    metadatas=metadatas,
    ids=[f"chunk_{i}" for i in range(len(chunks))]
)

print(f"Inserted {len(chunks)} chunks into ChromaDB.")
