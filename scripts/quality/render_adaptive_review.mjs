/** Render all comparison answers with the same ReactMarkdown/GFM packages as the app. */
import fs from "node:fs";
import path from "node:path";
import { createRequire } from "node:module";
import { pathToFileURL } from "node:url";

const require = createRequire(path.resolve("frontend/package.json"));
const React = (await import(pathToFileURL(require.resolve("react")).href)).default;
const { renderToStaticMarkup } = await import(pathToFileURL(require.resolve("react-dom/server")).href);
const ReactMarkdown = (await import(pathToFileURL(require.resolve("react-markdown")).href)).default;
const remarkGfm = (await import(pathToFileURL(require.resolve("remark-gfm")).href)).default;
const h = React.createElement;
const directory = path.resolve(process.argv[2] ?? "docs/evaluation/adaptive-response");
const data = JSON.parse(fs.readFileSync(path.join(directory, "review-data.json"), "utf8"));
const labels = { before: "修改前", candidate: "修改後" };
const style = `:root{font-family:system-ui,sans-serif;color:#31291f;background:#faf7f2;line-height:1.75}body{margin:0}header{padding:32px max(24px,5vw);background:#573b28;color:white}main{max-width:1450px;margin:auto;padding:24px}nav{display:flex;flex-wrap:wrap;gap:12px}a{color:#934600}header a{color:white}.case{margin:35px 0 70px;scroll-margin-top:20px}.prompt{background:#fff1df;border-radius:16px;padding:18px 24px}.row{display:grid;grid-template-columns:1fr 1fr;gap:20px;margin:20px 0}.answer{background:white;border:1px solid #e5d8c9;padding:24px;border-radius:20px;min-width:0;overflow-wrap:anywhere}h1,h2,h3{line-height:1.35}.meta{font-size:13px;color:#75695e}.error{color:#a23016}.markdown-message strong{font-weight:800;color:#31291f}.markdown-message ul,.markdown-message ol{margin:.5rem 0 .8rem;padding-left:1.4rem}.markdown-message ul{list-style:disc}.markdown-message ol{list-style:decimal}.markdown-message li{margin:.3rem 0;padding-left:.1rem}.markdown-message li>p{margin:.25rem 0}.markdown-message blockquote{border-left:3px solid #dbb483;padding-left:16px}.markdown-message pre{white-space:pre-wrap}details{margin:12px 0}footer{padding:25px;background:#efe6da}@media(max-width:900px){.row{grid-template-columns:1fr}main{padding:14px}.answer{padding:18px}}`;
const markdown = text => h("div", { className: "markdown-message" }, h(ReactMarkdown, { remarkPlugins: [remarkGfm] }, text));
const document = h("html", { lang: "zh-Hant" },
  h("head", null, h("meta", { charSet: "utf-8" }), h("meta", { name: "viewport", content: "width=device-width,initial-scale=1" }),
    h("title", null, "彈性回覆：修改前後完整比較"), h("style", null, style)),
  h("body", null,
    h("header", null, h("h1", null, "彈性回覆：修改前後完整比較"),
      h("p", null, "六類合成案例，每類前後各三次，共 36 筆；全部呈現，未挑選最佳答案。使用同一模型、參數、歷史、摘要及凍結來源。"),
      h("p", null, "請比較重點是否容易找到、分點是否有必要、簡單問題是否直接，以及是否有新增沒有依據的內容。條列或粗體越多，不代表越好。甲方驗收與法律正確性仍待人工確認。"),
      h("nav", null, data.cases.map(item => h("a", { key: item.id, href: `#${item.id}` }, item.title)))),
    h("main", null, data.cases.map(item => h("section", { className: "case", id: item.id, key: item.id },
      h("h2", null, item.title), h("div", { className: "prompt" }, h("p", null, item.message),
        item.summary ? h("p", null, `摘要：${item.summary}`) : null,
        item.history.length ? h("details", null, h("summary", null, "固定對話歷史"), item.history.map((message, index) => h("p", { key: index }, `${message.role}：${message.content}`))) : null),
      [1, 2, 3].map(repetition => h("div", { className: "row", key: repetition }, ["before", "candidate"].map(variant => {
        const answer = item.groups[variant].find(value => value.repetition === repetition);
        return h("article", { className: "answer", key: variant }, h("h3", null, `${labels[variant]} · 第 ${repetition} 次`),
          h("p", { className: "meta" }, `${answer.model_calls} 次模型呼叫 · ${answer.total_seconds} 秒 · `, h("a", { href: answer.result_path }, "完整 JSON")),
          answer.status !== "ok" ? h("p", { className: "error" }, "這次未完成有效回覆；下方可能只含部分文字。") : null,
          markdown(answer.text), answer.question ? h("p", null, `後續追問：${answer.question}`) : null,
          answer.source_labels.length ? h("details", null, h("summary", null, "取得的來源標籤"), h("p", { className: "meta" }, answer.source_labels.join("、"))) : null);
      })))))),
    h("footer", null, "本頁僅呈現本機比較結果；不是正式服務或法律正確性證明。", h("a", { href: "README.md" }, "比較方法與限制"))));
fs.writeFileSync(path.join(directory, "comparison.html"), "<!doctype html>\n" + renderToStaticMarkup(document));
console.log(JSON.stringify({ output: path.join(directory, "comparison.html"), cases: data.cases.length, answers: data.cases.length * 6 }));
