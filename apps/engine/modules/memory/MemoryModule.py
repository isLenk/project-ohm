import openai
import fastapi
from contextlib import asynccontextmanager
from qdrant_client import models, QdrantClient
from sentence_transformers import SentenceTransformer
from pydantic import BaseModel
encoder = None
client = None

import uuid

url = "http://localhost:6333"

@asynccontextmanager
async def lifespan(app: fastapi.FastAPI):
    global encoder, client
    encoder = SentenceTransformer('all-MiniLM-L6-v2')
    # client = QdrantClient(":memory:")
    client = QdrantClient(url=url)
    
    yield  # This will run the app
    # Cleanup resources here if needed

app = fastapi.FastAPI(lifespan=lifespan)

def check_important(data):
    client = openai.OpenAI(api_key="0608da5d28eb10cea2914f3de0f3ddba", base_url="http://127.0.0.1:5000/v1")
    response = client.chat.completions.create(
        model="gpt-3.5-turbo",
        messages=[
            {"role": "system", "content": "Determine if the following sentence is either a memory/fact/preference (but not question) or not. Return only either 'yes' or 'no' or else I will be fired."},
            {"role": "user", "content": data}
        ]
    )
    answer = None
    
    if response.choices and response.choices[0].message:
        answer = response.choices[0].message.content.strip().lower()
    
    # Return 'yes' or 'no' or redo if hallucination occurs
    return answer if answer in ["yes", "no"] else check_important(data)

@app.get("/health")
async def health():
    return {"status": "ok"}

async def log_memory(character: str, input_text: str):
    if not client:
        raise ValueError("Qdrant client is not initialized.")

    # Check that the collection exists, create if not
    if not client.collection_exists(collection_name=character):
        print(f"Creating collection for character: {character}")
        client.create_collection(
            collection_name=character,
            vectors_config=models.VectorParams(
                size=encoder.get_sentence_embedding_dimension(), 
                distance=models.Distance.COSINE)
        ) 
    
    # Encode the input text
    vector = encoder.encode(input_text).tolist()

    # Prepare the point to insert
    point = models.PointStruct(
        id=str(uuid.uuid4()),  # Generate a unique ID for the point
        vector=vector,
        payload={
            "input": input_text
        }
    )

    # Insert the point into the collection
    client.upsert(
        collection_name=character,
        points=[point]
    )

async def get_memory(character: str, input_text: str):
    if not client:
        raise ValueError("Qdrant client is not initialized.")
    
    # Ensure the collection exists
    if not client.collection_exists(collection_name=character):
        return None
    
    # Encode the input text
    vector = encoder.encode(input_text).tolist()

    # Search for similar points in the collection
    search_result = client.query_points(
        collection_name=character,
        query=vector,
        limit=3  # Get the most similar point
    ).points

    if search_result:
        return [point.payload for point in search_result]
    
    return None

class RequestData(BaseModel):
    character: str
    input: str

@app.post("/send")
async def send(request: RequestData):
    print(f"Received data: {request.input}")
    response = check_important(request.input)
    print(f"Response from check_important: {response}")

    should_remember = response == "yes" # Convert to bool.
    
    if should_remember:
        print(f"Logging memory for character: {request.character}")
        await log_memory(request.character, request.input)

    return response

@app.get("/get_memory")
async def get_memory_endpoint(character: str, input_text: str):
    print(f"Retrieving memory for character: {character}")

    memory = await get_memory(character, input_text)
    if memory:
        return {"memory": memory}
    else:
        return {"memory": "No memory found."}
    
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
