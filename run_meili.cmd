REM Fetch the latest version of Meilisearch image from DockerHub
docker pull getmeili/meilisearch:v1.12

REM Launch Meilisearch in development mode with a master key
docker run -it --rm -p 7700:7700 -e MEILI_ENV=development -e MEILI_MASTER_KEY=9wZhgPMXjQlLDO6HYdUDrBRAhSERQNl7GMLJ-JtIcpQ -v "C:/Users/talba/Documents/meili_data:/meili_data" getmeili/meilisearch:v1.12
REM Use ${pwd} instead of $(pwd) in PowerShell
pause