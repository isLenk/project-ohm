import Box from "./components/Box";
import EventLog from "./components/EventLog";
import Header from "./components/Header";

function App() {
	return (
		<div className="bg-[#2B2B2B] w-full h-full absolute py-8 px-14">
			<Header />
			<br />
			<Box>
				<ul>
					<li>Active</li>
				</ul>
			</Box>
			<br />
			<EventLog />
		</div>
	);
}

export default App;
