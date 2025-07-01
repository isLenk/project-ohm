import React from 'react'
import Box from "../components/Box";
import EventLog from "../components/EventLog";
import LogEntry from '../components/LogEntry';
import { useState, useEffect } from 'react'
const Dashboard = () => {
  const [logs, setLogs] = useState([])
  useEffect(() => {
    const fetchLogs = async () => {
      try {
        const response = await window.api.getLogs(25)
        console.log('Fetched logs:', response)
        setLogs(response)
      } catch (error) {
        console.error('Error fetching logs:', error)
      }
    }
    fetchLogs()
  }, [])

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
    <div className="py-2 grid grid-cols-8 grid-rows-4">
      <div className="col-start-1 col-span-4 row-start-1 row-span-1">
        <h1 className="text-2xl font-bold">What is this?</h1>
        Welcome to the OHM interface. Here, you will be able to review logs, customize what virtual
        character is active, and configure modules
      </div>

      <div className="col-start-5 col-span-3 row-start-1 row-span-4 ">
        <h1 className="text-2xl font-bold">Log</h1>
        <ul className="overflow-y-auto w-fit gap-1 flex flex-col">
          {logs.map(({ log_type, message }, index) => (
            <LogEntry key={index} log_type={log_type} value={message} />
          ))}
        </ul>
      </div>
    </div>
  )
}
  /* <div className="col-start-1 row-start-1 col-span-4 row-span-1">
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
      </div> */
export default Dashboard