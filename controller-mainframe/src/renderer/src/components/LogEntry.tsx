import React from 'react'
import { GoInfo } from "react-icons/go";
import { FaQuestionCircle } from "react-icons/fa";
import { FaBrain } from "react-icons/fa";
import { RxUpdate} from "react-icons/rx";
import { MdOutlineOutput, MdOutlineInput } from "react-icons/md";
import { FaCircleXmark } from "react-icons/fa6";
const LogEntry = ({key, log_type, value}) => {
  
    const className = `flex items-center gap-2 p-2 rounded-lg ${
        log_type === 'error' ? 'bg-red-500/20 text-white' :
        log_type === 'update' ? 'bg-yellow-500/20 text-white' :
        log_type === 'input' ? ' text-white' :
        log_type === 'output' ? 'text-white' :
        log_type === 'info' ? 'bg-gray-600/20 text-white' : ''
    }`

    // const className = `flex items-center gap-2 p-2 rounded-lg ${
    //     log_type === 'error' ? 'text-red-500' :
    //     log_type === 'update' ? 'text-yellow-500' :
    //     log_type === 'input' ? 'text-blue-500' :
    //     log_type === 'output' ? 'text-green-500' :
    //     log_type === 'info' ? 'text-gray-600' : ''
    // }`


    let image_icon = null;
    // switch (log_type) {
    //     case 'error':
    //         image_icon = <FaCircleXmark className="text-white" />;
    //         break;
    //     case 'update':
    //         image_icon = <RxUpdate className="text-white" />;
    //         break;
    //     case 'input':
    //         image_icon = <MdOutlineInput className="text-white" />;
    //         break;
    //     case 'output':
    //         image_icon = <MdOutlineOutput className="text-white" />;
    //         break;
    //     case 'info':
    //         image_icon = <FaBrain className="text-white" />;
    //         break;
    //     default:
    //         image_icon = <FaQuestionCircle className="text-white text-gray-500" />;
    // }
    switch (log_type) {
        case 'error':
            image_icon = <FaCircleXmark className="text-red-500" />;
            break;
        case 'update':
            image_icon = <RxUpdate className="text-yellow-500" />;
            break;
        case 'input':
            image_icon = <MdOutlineInput className="text-gray-200" />;
            break;
        case 'output':
            image_icon = <MdOutlineOutput className="text-white" />;
            break;
        case 'info':
            image_icon = <FaBrain className="text-white" />;
            break;
        default:
            image_icon = <FaQuestionCircle className="text-gray-500" />;
    }

    return (
        <li key={key} className={className}>

        {image_icon}
            <span>{value}</span>
        </li>
    )
}

export default LogEntry