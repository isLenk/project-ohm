from RealtimeTTS import TextToAudioStream, SystemEngine

engine = SystemEngine() # replace with your TTS engine
stream = TextToAudioStream(engine)
stream.feed("This is an insanely long sentence about the state of the art.")
stream.play()