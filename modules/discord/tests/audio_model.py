
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
