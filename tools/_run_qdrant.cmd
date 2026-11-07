
docker run -p 6333:6333 -p 6334:6334  --rm --name qdrant -v "C:/Users/talba/Documents/qdrant_storage:/qdrant/storage:z" qdrant/qdrant
pause
REM docker run -p 6333:6333 -p 6334:6334 --name qdrant -v "C:/Users/talba/Documents/qdrant_storage:/qdrant/storage:z" qdrant/qdrant 
