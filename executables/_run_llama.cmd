cd ..\..\llama.cpp
echo Loading LLM
bin\llama-server.exe -m models\Gemma-4-E4B-Uncensored-HauhauCS-Aggressive-Q5_K_M.gguf --mmproj models\mmproj-Gemma-4-E4B-Uncensored-HauhauCS-Aggressive-f16.gguf --alias gemma-4-e4b-uncensored --jinja --reasoning off -ngl 99 -c 32768 --port 5000
pause
