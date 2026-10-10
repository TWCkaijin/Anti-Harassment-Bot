import { createRoot } from "react-dom/client";
import ValidationPage from "./ValidationPage";
import App from "../App";
import "../index.css";

createRoot(document.getElementById("root")!).render(<App><ValidationPage /></App>);
