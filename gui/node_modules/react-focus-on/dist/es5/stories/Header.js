"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.Header = void 0;
var jsx_runtime_1 = require("react/jsx-runtime");
var Button_1 = require("./Button");
require("./header.css");
var Header = function (_a) {
    var user = _a.user, onLogin = _a.onLogin, onLogout = _a.onLogout, onCreateAccount = _a.onCreateAccount;
    return ((0, jsx_runtime_1.jsx)("header", { children: (0, jsx_runtime_1.jsxs)("div", { className: "storybook-header", children: [(0, jsx_runtime_1.jsxs)("div", { children: [(0, jsx_runtime_1.jsx)("svg", { width: "32", height: "32", viewBox: "0 0 32 32", xmlns: "http://www.w3.org/2000/svg", children: (0, jsx_runtime_1.jsxs)("g", { fill: "none", fillRule: "evenodd", children: [(0, jsx_runtime_1.jsx)("path", { d: "M10 0h12a10 10 0 0110 10v12a10 10 0 01-10 10H10A10 10 0 010 22V10A10 10 0 0110 0z", fill: "#FFF" }), (0, jsx_runtime_1.jsx)("path", { d: "M5.3 10.6l10.4 6v11.1l-10.4-6v-11zm11.4-6.2l9.7 5.5-9.7 5.6V4.4z", fill: "#555AB9" }), (0, jsx_runtime_1.jsx)("path", { d: "M27.2 10.6v11.2l-10.5 6V16.5l10.5-6zM15.7 4.4v11L6 10l9.7-5.5z", fill: "#91BAF8" })] }) }), (0, jsx_runtime_1.jsx)("h1", { children: "Acme" })] }), (0, jsx_runtime_1.jsx)("div", { children: user ? ((0, jsx_runtime_1.jsxs)(jsx_runtime_1.Fragment, { children: [(0, jsx_runtime_1.jsxs)("span", { className: "welcome", children: ["Welcome, ", (0, jsx_runtime_1.jsx)("b", { children: user.name }), "!"] }), (0, jsx_runtime_1.jsx)(Button_1.Button, { size: "small", onClick: onLogout, label: "Log out" })] })) : ((0, jsx_runtime_1.jsxs)(jsx_runtime_1.Fragment, { children: [(0, jsx_runtime_1.jsx)(Button_1.Button, { size: "small", onClick: onLogin, label: "Log in" }), (0, jsx_runtime_1.jsx)(Button_1.Button, { primary: true, size: "small", onClick: onCreateAccount, label: "Sign up" })] })) })] }) }));
};
exports.Header = Header;
