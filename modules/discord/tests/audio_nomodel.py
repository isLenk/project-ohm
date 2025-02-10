
    async def test_audio_no_model(self, vc, message):
        
        wav_file = "janiston.wav"
        chunk_count = 1
        chunk_counter = 0
        chunks = np.array([])

        audio_buffer = io.BytesIO()
        # Read wav file chunk by chunk
        with wave.open(wav_file, "rb") as f:

            sample_rate = f.getframerate()
            chunk = f.readframes(sample_rate * 1)

            while chunk:
                chunks = np.append(chunks, chunk)

                if (chunk_counter := chunk_counter + 1) == chunk_count:
                    # Filll buffer
                    AudioFix.fill_wav_buffer(audio_buffer, chunks)
                    
                    self.voice.audio_out_queue.put_nowait(audio_buffer)

                    audio_buffer = io.BytesIO()
                    chunk_counter = 0
                    chunks = np.array([])
                chunk = f.readframes(sample_rate * 1)

        if len(chunks) > 0:
            print(f"loading remaining {len(chunks)} chunks")
            AudioFix.fill_wav_buffer(audio_buffer, chunks)
            self.voice.audio_out_queue.put_nowait(audio_buffer)
                
        
        await self.voice.tts.finish_input()
        return "Okey deoky"
