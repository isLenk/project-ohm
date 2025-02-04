from RealtimeTTS import TextToAudioStream, CoquiEngine

if __name__ == "__main__":
    engine = CoquiEngine(model_name="xtts_v2", voice="janiston.wav") # replace with your TTS engine
    stream = TextToAudioStream(engine)
    stream.feed("""adwad""")
    stream.play()