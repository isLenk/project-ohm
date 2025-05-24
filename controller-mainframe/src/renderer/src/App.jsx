
function App() {

  const required_modules = {
    "NLP API": {
      code: "nlp_api",
      description: "Natural Language Processing API",
      required: true,
      ready: false,
    },
    "Text Generation API": {
      code: "text_generation_api",
      description: "Text Generation API",
      required: true,
      ready: false,
    }
  }

  return (
    <div className="p-4 grid grid-cols-4 grid-rows-8 h-screen">
      <h1 className="text-4xl font-bold mb-4">Activity</h1>
      <ul className="col-span-4 row-span-1 overflow-y-auto">
        {
          Object.entries(required_modules).map(([name, module]) => (
            <li key={name} className="flex items-center gap-4">
              <div className={`w-3 h-3 rounded-full ${module.ready ? 'bg-green-400' : 'bg-red-400'}`} />
              {name}
            </li>
          ))}
      </ul>
      <button
        className="px-4 py-2 bg-blue-500 text-white rounded hover:bg-blue-600"
      >
        
      </button>
    </div>
  )
}

export default App
