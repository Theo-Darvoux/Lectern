import { Project, Node } from "ts-morph";
import * as fs from "fs";
import * as path from "path";

const MESSAGES_DIR = "messages";
const EN_PATH = path.join(MESSAGES_DIR, "en.json");
const FR_PATH = path.join(MESSAGES_DIR, "fr.json");

function getFlatKeys(obj: Record<string, unknown>, prefix = ""): Map<string, string> {
  const keys = new Map<string, string>();
  for (const key in obj) {
    const fullKey = prefix ? `${prefix}.${key}` : key;
    if (typeof obj[key] === "object" && obj[key] !== null && !Array.isArray(obj[key])) {
      const subKeys = getFlatKeys(obj[key] as Record<string, unknown>, fullKey);
      subKeys.forEach((v, k) => keys.set(k, v));
    } else if (typeof obj[key] === "string") {
      keys.set(fullKey, obj[key] as string);
    }
  }
  return keys;
}

function findDuplicates(filePath: string): string[] {
  const content = fs.readFileSync(filePath, "utf-8");
  const lines = content.split("\n");
  const duplicates: string[] = [];
  const stack: Set<string>[] = [new Set()];
  
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i].trim();
    if (line.includes("{")) stack.push(new Set());
    if (line.includes("}")) stack.pop();
    
    const match = line.match(/^"([^"]+)":/);
    if (match) {
      const key = match[1];
      const currentScope = stack[stack.length - 1];
      if (currentScope && currentScope.has(key)) {
        duplicates.push(`${filePath}:${i + 1} -> Duplicate key: "${key}"`);
      }
      currentScope?.add(key);
    }
  }
  return duplicates;
}

function extractPlaceholders(text: string): string[] {
  const placeholders = new Set<string>();
  let depth = 0;
  for (let i = 0; i < text.length; i++) {
    const char = text[i];
    if (char === "{") {
      depth++;
      if (depth === 1) {
        let j = i + 1;
        let varName = "";
        while (j < text.length && text[j] !== "}" && text[j] !== "," && text[j] !== " ") {
          varName += text[j];
          j++;
        }
        if (varName && varName !== "#") placeholders.add(varName);
      }
    } else if (char === "}") {
      depth--;
    }
  }
  return Array.from(placeholders).sort();
}

function unwrapAssertions(node: Node): Node {
  if (Node.isAsExpression(node) || Node.isTypeAssertion(node)) {
    return unwrapAssertions(node.getExpression());
  }
  if (Node.isParenthesizedExpression(node)) {
    return unwrapAssertions(node.getExpression());
  }
  return node;
}

function getPossibleStringValues(node: Node): string[] | null {
  const unwrapped = unwrapAssertions(node);
  if (Node.isStringLiteral(unwrapped) || Node.isNoSubstitutionTemplateLiteral(unwrapped)) {
    return [unwrapped.getLiteralValue()];
  }
  
  if (Node.isBinaryExpression(unwrapped)) {
    const left = unwrapAssertions(unwrapped.getLeft());
    const right = unwrapAssertions(unwrapped.getRight());
    const op = unwrapped.getOperatorToken().getText();
    if (op === "+") {
      const leftVals = getPossibleStringValues(left);
      const rightVals = getPossibleStringValuesFromType(right.getType());
      if (leftVals && rightVals) {
        const result: string[] = [];
        for (const l of leftVals) {
          for (const r of rightVals) {
            result.push(l + r);
          }
        }
        return result;
      }
    }
  }

  return getPossibleStringValuesFromType(unwrapped.getType());
}

function getPossibleStringValuesFromType(type: any): string[] | null {
  if (type.isStringLiteral()) {
    return [type.getLiteralValue()];
  }
  if (type.isUnion()) {
    const values: string[] = [];
    for (const subType of type.getUnionTypes()) {
      if (subType.isStringLiteral()) {
        values.push(subType.getLiteralValue());
      } else {
        return null;
      }
    }
    return values;
  }
  return null;
}

const project = new Project({
  tsConfigFilePath: "tsconfig.json",
});

const enMessages = JSON.parse(fs.readFileSync(EN_PATH, "utf-8"));
const frMessages = JSON.parse(fs.readFileSync(FR_PATH, "utf-8"));

const enMap = getFlatKeys(enMessages);
const frMap = getFlatKeys(frMessages);
const enKeys = Array.from(enMap.keys());

console.log(`\n\x1b[1m--- 1. File Integrity ---\x1b[0m`);
const enDuplicates = findDuplicates(EN_PATH);
const frDuplicates = findDuplicates(FR_PATH);
if (enDuplicates.length > 0 || frDuplicates.length > 0) {
    [...enDuplicates, ...frDuplicates].forEach(d => console.error(`\x1b[31m❌ ${d}\x1b[0m`));
} else {
    console.log(`\x1b[32m✅ No duplicate keys found in raw files\x1b[0m`);
}

console.log(`\n\x1b[1m--- 2. Key Consistency ---\x1b[0m`);
const missingInFr = enKeys.filter(k => !frMap.has(k));
const extraInFr = Array.from(frMap.keys()).filter(k => !enMap.has(k));

if (missingInFr.length > 0) {
  console.error(`\x1b[31m❌ Missing in fr.json (${missingInFr.length}):\x1b[0m`);
  missingInFr.forEach(k => console.error(`  - ${k}`));
} else {
  console.log(`\x1b[32m✅ All English keys are present in French\x1b[0m`);
}

if (extraInFr.length > 0) {
  console.warn(`\x1b[33m⚠️ Extra keys in fr.json (${extraInFr.length}):\x1b[0m`);
  extraInFr.slice(0, 10).forEach(k => console.warn(`  - ${k}`));
}

console.log(`\n\x1b[1m--- 3. Placeholder Consistency ---\x1b[0m`);
let placeholderErrors = 0;
enMap.forEach((enVal, key) => {
    const frVal = frMap.get(key);
    if (frVal) {
        const enPlaceholders = extractPlaceholders(enVal);
        const frPlaceholders = extractPlaceholders(frVal);
        if (enPlaceholders.join(",") !== frPlaceholders.join(",")) {
            placeholderErrors++;
            console.error(`\x1b[31m❌ Mismatch in "${key}":\x1b[0m`);
            console.error(`   EN: ${enVal} (\x1b[36m${enPlaceholders.join(", ") || "none"}\x1b[0m)`);
            console.error(`   FR: ${frVal} (\x1b[36m${frPlaceholders.join(", ") || "none"}\x1b[0m)`);
        }
    }
});
if (placeholderErrors === 0) console.log(`\x1b[32m✅ All placeholders match between languages\x1b[0m`);

console.log(`\n\x1b[1m--- 4. Code Usage Scan ---\x1b[0m`);

const files = project.getSourceFiles(["src/**/*.tsx", "src/**/*.ts"]);
const usedKeys = new Set<string>();
const missingKeys = new Set<{ key: string, file: string, line: number }>();
const dynamicUsages: { file: string, line: number, text: string }[] = [];

function addUsedKey(key: string, file: string, line: number) {
    usedKeys.add(key);
    if (!enMap.has(key)) {
        missingKeys.add({ key, file, line });
    }
}

function protectNamespace(prefix: string) {
    if (!prefix) return;
    const cleanPrefix = prefix.endsWith(".") ? prefix.slice(0, -1) : prefix;
    enKeys.filter(k => k === cleanPrefix || k.startsWith(cleanPrefix + ".")).forEach(k => usedKeys.add(k));
}

for (const file of files) {
  const filePath = file.getFilePath().replace(process.cwd(), "");
  
  file.forEachDescendant((node) => {
    // 1. Check for useTranslations namespaces
    if (Node.isVariableDeclaration(node)) {
        const initializer = node.getInitializer();
        if (initializer && Node.isCallExpression(initializer)) {
            const callName = initializer.getExpression().getText();
            if (callName === "useTranslations") {
                const args = initializer.getArguments();
                const namespace = args.length > 0 && (Node.isStringLiteral(args[0]) || Node.isNoSubstitutionTemplateLiteral(args[0])) 
                    ? args[0].getLiteralValue() 
                    : null;
                
                const nameNode = node.getNameNode();
                if (Node.isIdentifier(nameNode)) {
                    const references = nameNode.findReferencesAsNodes();
                    for (const ref of references) {
                        // Ensure the reference is in the same file
                        if (ref.getSourceFile() !== file) continue;

                        let child: Node | undefined = ref;
                        while (child && !Node.isCallExpression(child)) {
                            child = child.getParent();
                        }
                        if (child) {
                            const expr = child.getExpression();
                            const isDirectCall = expr === ref;
                            const isPropertyCall = Node.isPropertyAccessExpression(expr) && expr.getExpression() === ref;
                            if (isDirectCall || isPropertyCall) {
                                const tArgs = child.getArguments();
                                if (tArgs.length > 0) {
                                    const arg = tArgs[0];
                                    const line = child.getStartLineNumber();
                                    const unwrapped = unwrapAssertions(arg);
                                    const resolvedValues = getPossibleStringValues(unwrapped);
                                    
                                    if (Node.isStringLiteral(unwrapped) || Node.isNoSubstitutionTemplateLiteral(unwrapped)) {
                                        const subKey = unwrapped.getLiteralValue();
                                        addUsedKey(namespace ? `${namespace}.${subKey}` : subKey, filePath, line);
                                    } else if (resolvedValues && resolvedValues.length > 0) {
                                        resolvedValues.forEach((val) => {
                                            addUsedKey(namespace ? `${namespace}.${val}` : val, filePath, line);
                                        });
                                    } else if (Node.isTemplateExpression(unwrapped)) {
                                        const head = unwrapped.getHead().getLiteralText();
                                        protectNamespace(namespace ? `${namespace}.${head}` : head);
                                        dynamicUsages.push({ file: filePath, line: line, text: child.getText() });
                                    } else {
                                        if (namespace) protectNamespace(namespace);
                                        dynamicUsages.push({ file: filePath, line: line, text: child.getText() });
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
    }
    
    /* Global t() check removed to avoid false positives with local translators */


    // 2b. Handle destructured t: const { t } = someObject (e.g., const { t } = actions)
    if (Node.isVariableDeclaration(node)) {
        const bindingPattern = node.getNameNode();
        const initializer = node.getInitializer();
        if (Node.isObjectBindingPattern(bindingPattern) && initializer) {
            for (const element of bindingPattern.getElements()) {
                const elName = element.getNameNode();
                const propNameNode = element.getPropertyNameNode();
                const propKey = propNameNode
                    ? propNameNode.getText()
                    : Node.isIdentifier(elName) ? elName.getText() : null;
                if (propKey !== 't' || !Node.isIdentifier(elName)) continue;

                // Resolve the namespace by tracing initializer.t -> its declaration -> useTranslations
                let namespace: string | null = null;
                const tSymbol = initializer.getType().getProperty('t');
                if (tSymbol) {
                    outer:
                    for (const decl of tSymbol.getDeclarations()) {
                        if (Node.isVariableDeclaration(decl)) {
                            // Direct: const t = useTranslations("Browse")
                            const tInit = decl.getInitializer();
                            if (tInit && Node.isCallExpression(tInit) && tInit.getExpression().getText() === 'useTranslations') {
                                const nsArg = tInit.getArguments()[0];
                                if (nsArg && (Node.isStringLiteral(nsArg) || Node.isNoSubstitutionTemplateLiteral(nsArg))) {
                                    namespace = nsArg.getLiteralValue();
                                    break;
                                }
                            }
                        } else if (Node.isShorthandPropertyAssignment(decl)) {
                            // Shorthand in return { t, ... } — walk up to the enclosing function
                            // and find const t = useTranslations(ns) inside it
                            let ancestor: Node | undefined = decl.getParent();
                            while (ancestor) {
                                if (
                                    Node.isFunctionDeclaration(ancestor) ||
                                    Node.isArrowFunction(ancestor) ||
                                    Node.isFunctionExpression(ancestor)
                                ) {
                                    ancestor.forEachDescendant((n) => {
                                        if (namespace || !Node.isVariableDeclaration(n)) return;
                                        const nName = n.getNameNode();
                                        if (!Node.isIdentifier(nName) || nName.getText() !== 't') return;
                                        const nInit = n.getInitializer();
                                        if (nInit && Node.isCallExpression(nInit) && nInit.getExpression().getText() === 'useTranslations') {
                                            const nsArg = nInit.getArguments()[0];
                                            if (nsArg && (Node.isStringLiteral(nsArg) || Node.isNoSubstitutionTemplateLiteral(nsArg))) {
                                                namespace = nsArg.getLiteralValue();
                                            }
                                        }
                                    });
                                    break;
                                }
                                ancestor = ancestor.getParent();
                            }
                            if (namespace) break outer;
                        }
                    }
                }

                const references = elName.findReferencesAsNodes();
                for (const ref of references) {
                    if (ref.getSourceFile() !== file) continue;
                    let child: Node | undefined = ref;
                    while (child && !Node.isCallExpression(child)) {
                        child = child.getParent();
                    }
                    if (child) {
                        const expr = child.getExpression();
                        const isDirectCall = expr === ref;
                        const isPropertyCall = Node.isPropertyAccessExpression(expr) && expr.getExpression() === ref;
                        if (isDirectCall || isPropertyCall) {
                            const tArgs = child.getArguments();
                            if (tArgs.length > 0) {
                                const arg = tArgs[0];
                                const line = child.getStartLineNumber();
                                const unwrapped = unwrapAssertions(arg);
                                const resolvedValues = getPossibleStringValues(unwrapped);
                                if (Node.isStringLiteral(unwrapped) || Node.isNoSubstitutionTemplateLiteral(unwrapped)) {
                                    const subKey = unwrapped.getLiteralValue();
                                    addUsedKey(namespace ? `${namespace}.${subKey}` : subKey, filePath, line);
                                } else if (resolvedValues && resolvedValues.length > 0) {
                                    resolvedValues.forEach((val) => {
                                        addUsedKey(namespace ? `${namespace}.${val}` : val, filePath, line);
                                    });
                                } else if (Node.isTemplateExpression(unwrapped)) {
                                    const head = unwrapped.getHead().getLiteralText();
                                    protectNamespace(namespace ? `${namespace}.${head}` : head);
                                    dynamicUsages.push({ file: filePath, line, text: child.getText() });
                                } else {
                                    if (namespace) protectNamespace(namespace);
                                    dynamicUsages.push({ file: filePath, line, text: child.getText() });
                                }
                            }
                        }
                    }
                }
            }
        }
    }

    // 2c. Handle t passed as argument to utility functions (e.g., fileSize(bytes, t))
    if (Node.isFunctionDeclaration(node) || Node.isArrowFunction(node) || Node.isFunctionExpression(node)) {
        const funcParams = node.getParameters();
        const tParamIdx = funcParams.findIndex((p) => {
            const pName = p.getNameNode();
            return Node.isIdentifier(pName) && pName.getText() === 't';
        });
        if (tParamIdx !== -1) {
            const tParamNameNode = funcParams[tParamIdx].getNameNode();
            if (Node.isIdentifier(tParamNameNode)) {
                const tParamSymbol = tParamNameNode.getSymbol();
                const tCalls: Array<{ key: string | null; head?: string; line: number; text: string }> = [];
                node.forEachDescendant((inner) => {
                    if (!Node.isCallExpression(inner)) return;
                    const callExpr = inner.getExpression();
                    if (Node.isIdentifier(callExpr) && callExpr.getText() === 't' && callExpr.getSymbol() === tParamSymbol) {
                        const innerArgs = inner.getArguments();
                        if (innerArgs.length > 0) {
                            const arg = unwrapAssertions(innerArgs[0]);
                            const line = inner.getStartLineNumber();
                            const text = inner.getText();
                            if (Node.isStringLiteral(arg) || Node.isNoSubstitutionTemplateLiteral(arg)) {
                                tCalls.push({ key: arg.getLiteralValue(), line, text });
                            } else if (Node.isTemplateExpression(arg)) {
                                tCalls.push({ key: null, head: arg.getHead().getLiteralText(), line, text });
                            } else {
                                const resolved = getPossibleStringValues(arg);
                                if (resolved && resolved.length > 0) {
                                    resolved.forEach((v) => tCalls.push({ key: v, line, text }));
                                } else {
                                    tCalls.push({ key: null, line, text });
                                }
                            }
                        }
                    }
                });
                if (tCalls.length > 0) {
                    let funcIdentifier: Node | undefined;
                    if (Node.isFunctionDeclaration(node)) {
                        const nameNode = node.getNameNode();
                        if (nameNode) funcIdentifier = nameNode;
                    } else {
                        const parent = node.getParent();
                        if (parent && Node.isVariableDeclaration(parent)) {
                            const nameNode = parent.getNameNode();
                            if (Node.isIdentifier(nameNode)) funcIdentifier = nameNode;
                        }
                    }
                    if (funcIdentifier) {
                        const namespaces = new Set<string>();
                        for (const ref of funcIdentifier.findReferencesAsNodes()) {
                            const refParent = ref.getParent();
                            if (refParent && Node.isCallExpression(refParent) && refParent.getExpression() === ref) {
                                const callArgs = refParent.getArguments();
                                if (callArgs.length > tParamIdx) {
                                    const passedArg = unwrapAssertions(callArgs[tParamIdx]);
                                    if (Node.isIdentifier(passedArg)) {
                                        const passedSymbol = passedArg.getSymbol();
                                        if (passedSymbol) {
                                            for (const decl of passedSymbol.getDeclarations()) {
                                                if (Node.isVariableDeclaration(decl)) {
                                                    const init = decl.getInitializer();
                                                    if (init && Node.isCallExpression(init) && init.getExpression().getText() === 'useTranslations') {
                                                        const nsArg = init.getArguments()[0];
                                                        if (nsArg && (Node.isStringLiteral(nsArg) || Node.isNoSubstitutionTemplateLiteral(nsArg))) {
                                                            namespaces.add(nsArg.getLiteralValue());
                                                        }
                                                    }
                                                }
                                            }
                                        }
                                    }
                                }
                            }
                        }
                        for (const ns of namespaces) {
                            for (const call of tCalls) {
                                if (call.key !== null) {
                                    addUsedKey(`${ns}.${call.key}`, filePath, call.line);
                                } else if (call.head != null) {
                                    protectNamespace(`${ns}.${call.head}`);
                                    dynamicUsages.push({ file: filePath, line: call.line, text: call.text });
                                } else {
                                    protectNamespace(ns);
                                    dynamicUsages.push({ file: filePath, line: call.line, text: call.text });
                                }
                            }
                        }
                    }
                }
            }
        }
    }

    // 3. Special case for _onStatusUpdate wrapper in upload-client.ts
    if (Node.isCallExpression(node) && node.getExpression().getText() === "_onStatusUpdate") {
        const args = node.getArguments();
        if (args.length >= 2 && Node.isStringLiteral(args[1])) {
            addUsedKey(`Upload.${args[1].getLiteralValue()}`, filePath, node.getStartLineNumber());
        }
    }
  });
}

// 4. Hardcoded exceptions for dynamically passed t() functions
protectNamespace("AutoTitle");

const trulyUnused = enKeys.filter(k => !usedKeys.has(k));
if (trulyUnused.length > 0) {
    console.warn(`\n⚠️ Unused keys in en.json (${trulyUnused.length}):`);
    trulyUnused.slice(0, 20).forEach(k => console.warn(`  - ${k}`));
    if (trulyUnused.length > 20) console.warn(`  ... and ${trulyUnused.length - 20} more`);
} else {
    console.log(`\x1b[32m✅ All keys in en.json are used (based on static analysis)\x1b[0m`);
}

if (missingKeys.size > 0) {
    console.error(`\n❌ Missing translations found (${missingKeys.size}):`);
    Array.from(missingKeys).slice(0, 20).forEach(m => console.error(`  - ${m.file}:${m.line} -> Missing "${m.key}"`));
    if (missingKeys.size > 20) console.error(`  ... and ${missingKeys.size - 20} more`);
} else {
    console.log(`\x1b[32m✅ No missing static translations detected\x1b[0m`);
}

if (dynamicUsages.length > 0) {
    console.log(`\nℹ️ Found ${dynamicUsages.length} dynamic usages that couldn't be statically analyzed:`);
    dynamicUsages.slice(0, 5).forEach(u => console.log(`  - ${u.file}:${u.line} -> ${u.text}`));
}


console.log(`\nScan complete.`);
