import React from 'react'
import Box from "../components/Box";
import EventLog from "../components/EventLog";

const Dashboard = () => {
  const required_modules = {
    'NLP API': {
      code: 'nlp_api',
      description: 'Natural Language Processing API',
      required: true,
      ready: false
    },
    'Text Generation API': {
      code: 'text_generation_api',
      description: 'Text Generation API',
      required: true,
      ready: false
    }
  }
  
return (
  <>
  <div className="py-2 grid grid-cols-8 grid-rows-8 h-screen">
    <div className="col-start-1 row-start-1 col-span-4 row-span-1">
    <h1 className="text-2xl font-bold">Activity</h1>
    <ul className=" overflow-y-auto">
      {
        Object.entries(required_modules).map(([name, module]) => (
          <li key={name} className="flex items-center gap-4">
            <div className={`w-3 h-3 rounded-full ${module.ready ? 'bg-green-400' : 'bg-red-400'}`} />
            {name}
          </li>
        ))}
    </ul>
    </div>
  </div>
  <br />
  <EventLog />
  </>
)}

export default Dashboard