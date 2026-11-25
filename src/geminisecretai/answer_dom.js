// Extract one canonical representation per expression, without editing the page.
(root) => {
    const blocks = new Set(["P", "DIV", "LI", "TR", "PRE", "BLOCKQUOTE"]);
    function math(node) {
        if (node.nodeType === Node.TEXT_NODE) return node.textContent;
        const tag = node.localName;
        const children = [...node.childNodes].filter(n =>
            !["annotation", "annotation-xml"].includes(n.localName));
        const values = children.map(math);
        if (tag === "msub") return values[0] + values[1];
        if (tag === "msup") return values[0] + "^" + values[1];
        if (tag === "msubsup") return values[0] + values[1] + "^" + values[2];
        if (tag === "mfrac") return "(" + values[0] + ")/(" + values[1] + ")";
        if (tag === "msqrt") return "sqrt(" + values.join("") + ")";
        return values.join("");
    }
    function walk(node, mathBranch = false) {
        if (node.nodeType === Node.TEXT_NODE) return node.textContent;
        if (node.nodeType !== Node.ELEMENT_NODE) return "";
        if (node.matches("script,style,button,user-query,[role=button],.response-header," +
            "[data-test-id*=attachment],.attachment,.sr-only,.screen-reader-only")) return "";
        if (node.matches("math")) return math(node);
        if (node.matches(".katex,mjx-container")) {
            const mml = node.querySelector("math");
            if (mml) return math(mml);
            const visual = node.querySelector(".katex-html");
            if (visual) return walk(visual, true);
            return node.getAttribute("aria-label") || node.textContent;
        }
        if (node.hidden || (!mathBranch && node.getAttribute("aria-hidden") === "true")) return "";
        const css = getComputedStyle(node);
        if (css.display === "none" || css.visibility === "hidden") return "";
        if (node.tagName === "BR") return "\n";
        const value = [...node.childNodes].map(n => walk(n, mathBranch)).join("");
        if (node.tagName === "SUP") return "^" + value;
        if (node.tagName === "TD" || node.tagName === "TH") return value + "\t";
        return blocks.has(node.tagName) ? value + "\n" : value;
    }
    return walk(root).replace(/\u200b/g, "").replace(/[ \t]+\n/g, "\n")
        .replace(/\n{3,}/g, "\n\n").trim();
}
