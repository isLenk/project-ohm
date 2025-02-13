import { BrowserRouter, Routes, Route } from "react-router-dom";
import Dashboard from "./pages/Dashboard";
import Characters from "./pages/Characters";
import Template from "./pages/Template";

function App() {
	return (
		<BrowserRouter>
			<Routes>
				<Route path="/" element={<Template />}>
					<Route path="/" element={<Dashboard />} />
					<Route path="/characters" element={<Characters />} />
					<Route path="*" element={<h1>Not Found</h1>} />
				</Route>
			</Routes>
		</BrowserRouter>
	);
}

export default App;
