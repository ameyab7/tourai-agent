#!/bin/bash
docker rm -f overpass 2>/dev/null
docker run -d --platform linux/amd64 -e OVERPASS_MODE=serve -v overpass-data:/db/db -p 12345:80 --name overpass wiktorn/overpass-api

# Wait for supervisor to start, then fix /db permissions so fcgiwrap (nginx user) can reach the socket
echo "Waiting for container to start..."
sleep 5
docker exec overpass chmod 755 /db
echo "Overpass running at http://localhost:12345"
