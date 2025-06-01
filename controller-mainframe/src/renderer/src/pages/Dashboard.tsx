import React from 'react'
import Box from "../components/Box";
import EventLog from "../components/EventLog";
import LogEntry from '../components/LogEntry';

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
  
  const log_entries = [
    ['update', 'nlp_api restarted'],
    ['update', 'text_generation_api restarted'],
    ['error', 'nlp_api failed to start'],
    ['error', 'text_generation_api failed to start'],
    ['input', '"Hello, world!"'],
    ['output', '"Hello, world!"'],
    ['info', 'Memory updated!']
  ]

return (
  <div className="py-2 grid grid-cols-8 grid-rows-4">
    <div className="col-start-1 row-start-1 col-span-4 row-span-1">
      <h1 className="text-2xl font-bold">Registry</h1>
      <ul className="overflow-y-auto">
        {Object.entries(required_modules).map(([name, module]) => (
          <li key={name} className="flex items-center gap-4">
            <div
              className={`w-3 h-3 rounded-full ${module.ready ? 'bg-green-400' : 'bg-red-400'}`}
            />
            {name}
          </li>
        ))}
      </ul>
    </div>

    <div className="col-start-5 col-span-3 row-start-1 row-span-4 ">
    <h1 className="text-2xl font-bold">Log</h1>
    <ul className="overflow-y-auto w-fit gap-1 flex flex-col">
        {
          log_entries.map(([log_type, value], index) => (
            <LogEntry key={index} log_type={log_type} value={value} />
          ))
        }
      </ul>
    </div>
  </div>
)}

export default Dashboard