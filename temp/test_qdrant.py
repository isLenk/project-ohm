from qdrant_client import QdrantClient
from sentence_transformers import SentenceTransformer
import uuid
import numpy as np
from qdrant_client import models
import qdrant_client.http.models
# Load model - uses about 1.8GB VRAM for bge-small-en
print("Loading Model")
model = SentenceTransformer('BAAI/bge-small-en')

client = QdrantClient(host="192.168.2.92", port=6333, timeout=100, prefer_grpc=False)

print("Checking if user_facts collection exists")
if not client.collection_exists("user_facts"):
    client.create_collection(
        collection_name="user_facts",
        vectors_config=models.VectorParams(size=384, distance=models.Distance.COSINE, on_disk=True),
        optimizers_config=models.OptimizersConfigDiff(
            default_segment_number=5,
            indexing_threshold=0,
        ),
        quantization_config=models.BinaryQuantization(
            binary=models.BinaryQuantizationConfig(always_ram=True),
        ),
    )

def generate_unique_id():
    return uuid.uuid4().hex


def store_fact(fact: str, metadata: dict):
    # Generate embedding locally
    embedding = model.encode(fact, normalize_embeddings=True)
    # Store in Qdrant
    client.upsert(
        collection_name="user_facts",
        points=[models.PointStruct(
            id=generate_unique_id(),
            vector=embedding.tolist(),  # Convert numpy array to list
            payload={
                "text": fact,
                **metadata
            }
        )]
    )

def retrieve_relevant_facts(context: str, limit: int = 5):
    # Generate embedding locally
    context_embedding = model.encode(context, normalize_embeddings=True)
    
    results = client.search(
        collection_name="user_facts",
        query_vector=context_embedding.tolist(),
        limit=limit
    )
    
    return [hit.payload["text"] for hit in results]


# store an example fact
store_fact("The capital of Frachister is Pangione", {"source": "Wikipedia"})

# Retrieve facts relevant to a context
context = "WFrachister, what is its capital?"
retrieved_facts = retrieve_relevant_facts(context)
print(retrieved_facts)  # ['The capital of Frachister is Pangione']
