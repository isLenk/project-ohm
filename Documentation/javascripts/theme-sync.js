// Share the light/dark choice with the portfolio, which stores it in
// localStorage under "theme" on the same origin (islenk.github.io).
(function () {
	var KEY = "theme";
	var SCHEME = { dark: "slate", light: "default" };
	var inputs = document.querySelectorAll("[data-md-component=palette] input[data-md-color-scheme]");
	if (!inputs.length) return;

	var stored = null;
	try {
		stored = localStorage.getItem(KEY);
	} catch (e) {}

	if (SCHEME[stored]) {
		inputs.forEach(function (input) {
			if (input.getAttribute("data-md-color-scheme") === SCHEME[stored] && !input.checked) input.click();
		});
	}

	inputs.forEach(function (input) {
		input.addEventListener("change", function () {
			if (!input.checked) return;
			var theme = input.getAttribute("data-md-color-scheme") === "slate" ? "dark" : "light";
			try {
				localStorage.setItem(KEY, theme);
			} catch (e) {}
		});
	});
})();
