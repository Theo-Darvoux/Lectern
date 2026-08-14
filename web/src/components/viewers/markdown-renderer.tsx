"use client";

import React, { useMemo } from "react";
import ReactMarkdown, { defaultUrlTransform, type Components, type Options } from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkWikiLink from "remark-wiki-link";
import remarkMark from "@/lib/remark-mark";
import rehypeRaw from "rehype-raw";
import rehypeSanitize, { defaultSchema } from "rehype-sanitize";
import rehypeHighlight from "rehype-highlight";
import { Mermaid } from "./mermaid";
import { AsyncMaterialImage } from "./async-material-image";
import { Callout, CalloutType } from "./callout";
import { cn } from "@/lib/utils";

interface MarkdownRendererProps {
    content: string;
    materialId?: string;
    material?: Record<string, unknown>;
    className?: string;
    previewMode?: boolean;
    /** Resolve trusted image-only schemes before react-markdown sanitizes URLs. */
    resolveImageUrl?: (url: string) => string | null;
}

const sanitizeSchema = {
    ...defaultSchema,
    tagNames: [...(defaultSchema.tagNames || []), "mark"],
    attributes: {
        ...defaultSchema.attributes,
        code: [...(defaultSchema.attributes?.code || []), "className"],
        span: [...(defaultSchema.attributes?.span || []), "className"],
        img: [...(defaultSchema.attributes?.img || []), "className", "src", "alt", "loading"],
        mark: ["className"],
    },
    protocols: {
        ...defaultSchema.protocols,
        // Notebook attachments reach a caller-provided resolver after the AST
        // is sanitized. Unresolved attachment URLs are still rejected by
        // react-markdown's default URL transform.
        src: [...(defaultSchema.protocols?.src || []), "attachment"],
    },
};

const remarkPlugins: Options["remarkPlugins"] = [remarkGfm, remarkWikiLink, remarkMark];
const rehypePlugins: Options["rehypePlugins"] = [
    rehypeRaw,
    [rehypeSanitize, sanitizeSchema],
    rehypeHighlight,
];

function getTextFromChildren(children: React.ReactNode, depth = 0): string {
    if (depth > 10) return ""; // Prevent deep recursion DoS
    if (children === null || children === undefined) return "";
    if (typeof children === "string") return children;
    if (typeof children === "number") return children.toString();
    if (Array.isArray(children)) {
        return children.map((c) => getTextFromChildren(c, depth + 1)).join("");
    }
    const props = (children as { props?: { children?: React.ReactNode } })?.props;
    if (props?.children) {
        return getTextFromChildren(props.children, depth + 1);
    }
    return "";
}

export function MarkdownRenderer({ content, material, className, previewMode, resolveImageUrl }: MarkdownRendererProps) {
    const components: Components = useMemo(() => ({
        img: (props) => {
            const { src, alt } = props;
            return (
                <AsyncMaterialImage
                    src={(src as string) || ""}
                    alt={alt || ""}
                    material={material}
                    className="max-w-full rounded-lg shadow-sm"
                />
            );
        },
        a: (props) => {
            const { href, children, ...rest } = props;
            const isExternal = href?.startsWith("http");
            if (previewMode) {
                return (
                    <span className="text-primary/80 underline decoration-dotted">
                        {children}
                    </span>
                );
            }
            return (
                <a
                    href={href}
                    {...(isExternal
                        ? { target: "_blank", rel: "noopener noreferrer" }
                        : {})}
                    {...rest}
                >
                    {children}
                </a>
            );
        },
        table: ({ children, ...props }) => (
            <div className="overflow-x-auto">
                <table {...props}>{children}</table>
            </div>
        ),
        code: (props) => {
            const { className, children, node, ...rest } = props;
            const match = /language-(\w+)/.exec(className || "");
            const language = match ? match[1] : "";
            
            // Mermaid check (robust)
            const isMermaid = language === "mermaid" || (node?.properties?.className as string[])?.includes("language-mermaid");
            
            if (isMermaid) {
                if (previewMode) return null; // Skip mermaid in preview mode
                const chart = getTextFromChildren(children);
                return <Mermaid chart={chart.trim()} />;
            }
            return (
                <code className={className} {...rest}>
                    {children}
                </code>
            );
        },
        blockquote: ({ children }) => {
            const allChildren = React.Children.toArray(children);
            
            // Find the first actual block element, ignoring raw whitespace strings
            const firstBlockIndex = allChildren.findIndex(
                child => React.isValidElement(child) || (typeof child === "string" && child.trim() !== "")
            );
            
            if (firstBlockIndex === -1) {
                return <blockquote className="border-l-4 border-border pl-4 italic my-4">{children}</blockquote>;
            }

            const firstChild = allChildren[firstBlockIndex] as React.ReactElement<{ children?: React.ReactNode }>;
            
            // Extract children from the first block (usually a paragraph)
            const firstBlockChildren = firstChild.props?.children || firstChild;
            const pChildren = React.Children.toArray(firstBlockChildren);
            
            // Find the first actual inline content, ignoring raw whitespace strings
            const firstContentIndex = pChildren.findIndex(
                child => React.isValidElement(child) || (typeof child === "string" && child.trim() !== "")
            );
            
            if (firstContentIndex === -1) {
                return <blockquote className="border-l-4 border-border pl-4 italic my-4">{children}</blockquote>;
            }

            const firstPChild = pChildren[firstContentIndex];

            const firstNodeText = typeof firstPChild === "string" ? firstPChild : getTextFromChildren(firstPChild);

            if (firstNodeText) {
                // Match the marker, allowing a few random leading characters to avoid BOM or zero-width-space issues
                const match = firstNodeText.match(/^.{0,5}?\[!(\w+)\]([+-]?)/);
                
                if (match) {
                    const type = match[1].toLowerCase() as CalloutType;
                    const collapseMarker = match[2];
                    const collapsible = collapseMarker === "+" || collapseMarker === "-";
                    const defaultOpen = collapseMarker !== "-";

                    const textAfterMatch = firstNodeText.slice(match[0].length).replace(/^[ \t]+/, ''); // remove leading spaces
                    
                    const titleElements = [];
                    const bodyChildren = [];
                    let foundNewline = false;

                    const firstNewlineIndex = textAfterMatch.indexOf('\n');
                    if (firstNewlineIndex !== -1) {
                        const titlePart = textAfterMatch.slice(0, firstNewlineIndex);
                        if (titlePart) titleElements.push(titlePart);
                        
                        const bodyPart = textAfterMatch.slice(firstNewlineIndex + 1);
                        if (bodyPart.trim() !== '') bodyChildren.push(bodyPart);
                        
                        foundNewline = true;
                        bodyChildren.push(...pChildren.slice(firstContentIndex + 1));
                    } else {
                        if (textAfterMatch) titleElements.push(textAfterMatch);
                        
                        for (let i = firstContentIndex + 1; i < pChildren.length; i++) {
                            const child = pChildren[i];
                            if (foundNewline) {
                                bodyChildren.push(child);
                            } else if (typeof child === "string") {
                                const nlIndex = child.indexOf('\n');
                                if (nlIndex !== -1) {
                                    const titlePart = child.slice(0, nlIndex);
                                    if (titlePart) titleElements.push(titlePart);
                                    
                                    const bodyPart = child.slice(nlIndex + 1);
                                    if (bodyPart.trim() !== '') bodyChildren.push(bodyPart);
                                    
                                    foundNewline = true;
                                } else {
                                    titleElements.push(child);
                                }
                            } else {
                                titleElements.push(child);
                            }
                        }
                    }

                    // Clean up trailing quotes from the title if they were used for wrapping
                    if (titleElements.length > 0) {
                        const lastEl = titleElements[titleElements.length - 1];
                        if (typeof lastEl === "string") {
                            const strEl = lastEl as string;
                            const trimmed = strEl.trimEnd();
                            if (trimmed.endsWith("”") || trimmed.endsWith("\"") || trimmed.endsWith("'")) {
                                titleElements[titleElements.length - 1] = trimmed.slice(0, -1);
                            } else {
                                titleElements[titleElements.length - 1] = trimmed;
                            }
                        }
                    }

                    const otherBlocks = allChildren.slice(firstBlockIndex + 1);

                    const calloutContent = [];
                    if (bodyChildren.length > 0) {
                        calloutContent.push(React.cloneElement(firstChild, { ...firstChild.props, children: bodyChildren, key: "p-body" }));
                    }
                    calloutContent.push(...otherBlocks);

                    return (
                        <Callout 
                            type={type} 
                            collapsible={collapsible} 
                            defaultOpen={defaultOpen}
                            title={titleElements.length > 0 ? titleElements : undefined}
                        >
                            {calloutContent.length > 0 ? calloutContent : null}
                        </Callout>
                    );
                }
            }

            return <blockquote className="border-l-4 border-border pl-4 italic my-4">{children}</blockquote>;
        },
    }), [material, previewMode]);

    const urlTransform = useMemo<NonNullable<Options["urlTransform"]>>(
        () => (url, key, node) => {
            if (key === "src" && node.tagName === "img") {
                const resolved = resolveImageUrl?.(url);
                if (resolved) return resolved;
            }
            return defaultUrlTransform(url);
        },
        [resolveImageUrl],
    );

    return (
        <div className={cn(className, previewMode && "prose-sm pointer-events-none select-none")}>
            <ReactMarkdown
                remarkPlugins={remarkPlugins}
                rehypePlugins={rehypePlugins}
                components={components}
                urlTransform={urlTransform}
            >
                {content}
            </ReactMarkdown>
        </div>
    );
}
