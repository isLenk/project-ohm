import { BrowserRouter, Routes, Route } from "react-router-dom";
import Dashboard from "./pages/Dashboard";
import Characters from "./pages/Characters";
import Template from "./pages/Template";
import Page404 from "./pages/Page404";

function App() {
	return (
		<BrowserRouter>
			<Routes>
				<Route path="/" element={<Template />}>
					<Route path="/" element={<Dashboard />} />
					<Route path="/characters" element={<Characters />} />
					<Route path="*" element={<Page404 />} />
				</Route>
			</Routes>
		</BrowserRouter>
	);
}

export default App;
