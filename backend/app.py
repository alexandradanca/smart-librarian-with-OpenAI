import io
from flask import Flask, render_template, request, jsonify, send_file
import os
from config import (
    OPENAI_API_KEY,
    OPENAI_EMBEDDING_MODEL,
    OPENAI_CHAT_MODEL,
    CHROMA_API_KEY,
    CHROMA_TENANT,
    CHROMA_DATABASE
)
from openai import OpenAI
import chromadb
import json

#########################################################
####### Initialize Flask app and ChromaDB client ########
#########################################################
app = Flask(__name__, static_folder="static", template_folder="templates")

client = chromadb.CloudClient(
    api_key=CHROMA_API_KEY,
    tenant=CHROMA_TENANT,
    database=CHROMA_DATABASE
)
collection = client.get_or_create_collection(name="book_chunks")
openai_client = OpenAI(api_key=OPENAI_API_KEY)

#########################################################
################## Get Book Summary #####################
#########################################################
# Get the summary of a book by its title
# Use /summary <book_title> in the chat 
def get_summary_by_title(title: str) -> str:
    """
    Returns the summary for a given book title from book_summaries.json.
    Searches the JSON file for a book with the specified title (case-insensitive).
    Returns the summary if found, otherwise a not-found message.
    """
    with open(os.path.join(os.path.dirname(__file__), '../data/book_summaries.json'), encoding='utf-8') as f:
        books = json.load(f)
    for book in books:
        if book['title'].lower() == title.lower():
            return book['summary']
    return f"No summary found for title: {title}"

# Register tool for OpenAI function calling
openai_tools = [
    {
        "type": "function",
        "function": {
            "name": "get_summary_by_title",
            "description": "Get the summary of a book by its title.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "The title of the book."}
                },
                "required": ["title"]
            }
        }
    }
]

#########################################################
################### Main Chat UI ########################
#########################################################
@app.route("/")
def index():
    """
    Renders the main chat UI page.
    """
    return render_template("index.html")

@app.route("/ask", methods=["POST"])
def ask():
    """
    Main chat endpoint. Handles user questions and returns chatbot responses.
    - If the question starts with '/summary', returns a book summary (optionally translated).
    - Otherwise, performs RAG search and generates an answer using OpenAI and ChromaDB.
    - Handles polite fallback if no book matches are found.
    - Supports image generation and TTS features.
    """
    data = request.json
    question = data.get("question", "")
    history = data.get("history", [])
    # If user writes '/summary', call the summary tool
    if question.strip().startswith("/summary"):
        # Use OpenAI to detect desired output language
        lang_detect_prompt = f"What language does the user want the answer in? Respond only with the language name.\n\nUser request: {question}"
        lang_response = openai_client.chat.completions.create(
            model=OPENAI_CHAT_MODEL,
            messages=[{"role": "user", "content": lang_detect_prompt}]
        )
        desired_language = lang_response.choices[0].message.content.strip()
        title = question.strip()[len("/summary"):].strip()
        # Remove language request from title (for clarity)
        language_phrases = ["in limba romana", "în română", "in romanian", "in english", "en français", "auf deutsch"]
        for x in language_phrases:
            if x in title.lower():
                title = title.lower().replace(x, "").strip()
        # If title is empty or only contains a language phrase, extract last book title from history
        if not title or any(x in question.lower() for x in language_phrases):
            last_title = None
            # Search for last mentioned book title in previous bot responses
            with open(os.path.join(os.path.dirname(__file__), '../data/book_summaries.json'), encoding='utf-8') as f:
                books = json.load(f)
                book_titles = [book['title'] for book in books]
            for entry in reversed(history):
                if entry.get('role') == 'assistant':
                    mentioned = [t for t in book_titles if t in entry.get('content', '')]
                    if mentioned:
                        last_title = mentioned[-1]
                        break
            title = last_title if last_title else ""
        summary = get_summary_by_title(title) if title else "No book title found in previous answers."
        # Translate to desired language if needed
        if desired_language.lower() not in ["english", "en"] and summary and not summary.startswith("No book title"):
            translate_prompt = f"Translate the following book summary to {desired_language}:\n\n{summary}"
            response = openai_client.chat.completions.create(
                model=OPENAI_CHAT_MODEL,
                messages=[{"role": "user", "content": translate_prompt}]
            )
            summary = response.choices[0].message.content.strip()
        return jsonify({"answer": summary, "context": ""})
    # Detect the language of the question using OpenAI
    lang_prompt = f"What language is used in the following text? Answer only with the language name.\n\nText: {question}"
    lang_response = openai_client.chat.completions.create(
        model=OPENAI_CHAT_MODEL,
        messages=[{"role": "user", "content": lang_prompt}]
    )
    user_language = lang_response.choices[0].message.content.strip()
    data = request.json
    question = data.get("question", "")
    history = data.get("history", [])
    # Reformulate the question using LLM if there is history
    if history:
        # Use conversation history to create a standalone question
        messages = history + [
            {"role": "user", "content": f"Reformulate the following question as a standalone question, using the context of the conversation: {question}"}
        ]
        response = openai_client.chat.completions.create(
            model=OPENAI_CHAT_MODEL,
            messages=messages
        )
        standalone_question = response.choices[0].message.content.strip()
    else:
        standalone_question = question
    # Get embedding for the question using OpenAI
    embedding = openai_client.embeddings.create(model=OPENAI_EMBEDDING_MODEL, input=[standalone_question]).data[0].embedding
    # Query ChromaDB for relevant book chunks
    results = collection.query(
        query_embeddings=[embedding],
        n_results=3,
        include=["documents", "metadatas"]
    )
    # If no match is found, respond politely and list available themes
    if not results["documents"] or not results["documents"][0]:
        # Extract themes from ChromaDB (from metadatas)
        all_metadatas = collection.get()["metadatas"]
        themes = set()
        for meta in all_metadatas:
            if "themes" in meta:
                themes.update(meta["themes"])
        theme_list = sorted(list(themes))
        # Prompt for generating a polite response in the user's language
        polite_prompt = (
            f"Respond politely in {user_language} as a chatbot that did not find any suitable book for the requested topic. Explicitly state that you only have access to books in your database. Do not mention book titles! List only the available themes.\n\n"
            f"Question: {question}\n"
            f"Available themes: {', '.join(theme_list)}"
        )
        polite_response = openai_client.chat.completions.create(
            model=OPENAI_CHAT_MODEL,
            messages=[{"role": "user", "content": polite_prompt}]
        )
        polite_answer = polite_response.choices[0].message.content.strip()
        return jsonify({
            "answer": polite_answer,
            "context": "",
            "themes": theme_list
        })
    # Build context from ChromaDB results
    context = "\n".join(results["documents"][0])
    # If context is empty, do not generate a recommendation
    if not context.strip():
        return jsonify({
            "answer": "",
            "context": ""
        })
    # Generate answer with LLM, forcing it to use ONLY the context
    prompt = (
        f"Use only the information from the context below to answer the question. Do not invent titles or information that does not appear in the context. Answer in {user_language}.\n\nContext:\n{context}\n\nQuestion: {standalone_question}\nAnswer:"
    )
    response = openai_client.chat.completions.create(
        model=OPENAI_CHAT_MODEL,
        messages=[{"role": "user", "content": prompt}]
    )
    answer = response.choices[0].message.content.strip()
    # Check if the user requested an image
    image_keywords = ["image", "picture", "draw", "generate an image", "show me a picture"]
    image_url = None
    if any(kw.lower() in question.lower() for kw in image_keywords):
        try:
            # Generate image using OpenAI DALL-E
            img_response = openai_client.images.generate(
                model="dall-e-3",
                prompt=question,
                n=1,
                size="512x512"
            )
            image_url = img_response.data[0].url
        except Exception as e:
            image_url = None
    result = {"answer": answer, "context": context}
    if image_url:
        result["image_url"] = image_url
    return jsonify(result)

#########################################################
################### Generate Image ######################
#########################################################
@app.route("/generate_image", methods=["POST"])
def generate_image():
    """
    Endpoint for generating images using OpenAI DALL-E.
    Accepts a prompt and returns the generated image URL.
    """
    data = request.json
    prompt = data.get("prompt", "")
    try:
        response = openai_client.images.generate(
            model="dall-e-3",
            prompt=prompt,
            n=1,
            size="1024x1792"
        )
        url = response.data[0].url
        return jsonify({"url": url})
    except Exception as e:
        return jsonify({"url": "", "error": str(e)})

#########################################################
################### Text-to-Speech ######################
#########################################################
@app.route("/tts", methods=["POST"])
def tts():
    """
    Endpoint for Text-to-Speech (TTS) using OpenAI.
    Accepts text and voice, returns generated audio as an MP3 file.
    """
    data = request.json
    text = data.get("text", "")
    voice = data.get("voice", "alloy")  # Default voice
    try:
        response = openai_client.audio.speech.create(
            model="tts-1",
            voice=voice,
            input=text
        )
        audio_bytes = response.content
        return send_file(
            io.BytesIO(audio_bytes),
            mimetype="audio/mpeg",
            as_attachment=False,
            download_name="speech.mp3"
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    # Run the Flask app
    app.run(host="0.0.0.0", port=5000, debug=True)
