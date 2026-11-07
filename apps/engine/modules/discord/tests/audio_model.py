
    async def handle_request_stream(self, text):
        """Handle the request stream from the TTS module"""
        queue = self.audio_out_queue
        sample_rate = 24000
        min_buffer_size = sample_rate * 5
        out_buffer = io.BytesIO()
        self.is_playing = True
        
        async for chunk in self.tts.get_request_stream(text):
            out_buffer.write(chunk)

            if out_buffer.tell() >= min_buffer_size:
                out_buffer = await DC_Util.push_buffer_to_queue(
                    out_buffer, 
                    queue) 
        
        if out_buffer.tell() > 0:
            await DC_Util.push_buffer_to_queue(out_buffer, queue)
        
        while self.is_playing:
            await asyncio.sleep(0.3)        
        
    async def say(self, text):
        await self.handle_request_stream(text)
        while self.vc.is_playing() or self.is_playing:
            await asyncio.sleep(0.5)
            
async def test_audio(self, vc, message):
    # Propmt
    text_stream = self.make_stream_response(message)

    # for index, text in enumerate(self._openai_generator(text_stream)):
    for index, text in enumerate(["This is a really long message about the catapults."]):
        print("Feeding ->", text)
        if index == 0:
            await self.voice.handle_request_stream(text)
            # await asyncio.gather(self.voice.handle_request_stream(text))
        else:
            await self.voice.handle_request_stream(text)

            # await asyncio.gather(self.voice.handle_request_stream(text))
        # Poll while audio is being played
        while self.voice.vc.is_playing() or self.voice.is_playing:
            await asyncio.sleep(0.5)
    return "Okey deoky"
