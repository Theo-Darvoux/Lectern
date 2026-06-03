export interface NameSegment {
    text: string;
    font: string;
    color: string | null;
    bold: boolean;
    italic: boolean;
}

export interface FontDef {
    name: string;
    category: string;
    weights: string;
    italic: boolean;
}

export const AVAILABLE_FONTS: FontDef[] = [
    // Sans-serif
    { name: "Inter",             category: "Sans-serif",  weights: "300;400;700;900", italic: true  },
    { name: "Roboto",            category: "Sans-serif",  weights: "300;400;700;900", italic: true  },
    { name: "Open Sans",         category: "Sans-serif",  weights: "300;400;700;800", italic: true  },
    { name: "Lato",              category: "Sans-serif",  weights: "300;400;700;900", italic: true  },
    { name: "Poppins",           category: "Sans-serif",  weights: "300;400;700;900", italic: true  },
    { name: "Montserrat",        category: "Sans-serif",  weights: "300;400;700;900", italic: true  },
    { name: "Raleway",           category: "Sans-serif",  weights: "300;400;700;900", italic: true  },
    { name: "Nunito",            category: "Sans-serif",  weights: "300;400;700;900", italic: true  },
    { name: "DM Sans",           category: "Sans-serif",  weights: "300;400;700;900", italic: true  },
    { name: "Plus Jakarta Sans", category: "Sans-serif",  weights: "300;400;700;800", italic: true  },
    // Serif
    { name: "Playfair Display",  category: "Serif",       weights: "400;700;900",     italic: true  },
    { name: "Lora",              category: "Serif",       weights: "400;700",         italic: true  },
    { name: "Merriweather",      category: "Serif",       weights: "300;400;700;900", italic: true  },
    { name: "EB Garamond",       category: "Serif",       weights: "400;700",         italic: true  },
    // Monospace
    { name: "JetBrains Mono",    category: "Monospace",   weights: "400;700",         italic: true  },
    { name: "Fira Code",         category: "Monospace",   weights: "400;700",         italic: false },
    // Display
    { name: "Oswald",            category: "Display",     weights: "300;400;700",     italic: false },
    { name: "Bebas Neue",        category: "Display",     weights: "400",             italic: false },
    { name: "Righteous",         category: "Display",     weights: "400",             italic: false },
    { name: "Pacifico",          category: "Display",     weights: "400",             italic: false },
    // Handwriting
    { name: "Dancing Script",    category: "Handwriting", weights: "400;700",         italic: false },
    { name: "Caveat",            category: "Handwriting", weights: "400;700",         italic: false },
];

function fontFamilyParam(font: FontDef): string {
    const urlName = font.name.replace(/ /g, "+");
    if (font.italic) {
        const parts = font.weights.split(";").flatMap((w) => [`0,${w}`, `1,${w}`]);
        return `family=${urlName}:ital,wght@${parts.join(";")}`;
    }
    return `family=${urlName}:wght@${font.weights}`;
}

export function buildGoogleFontsUrl(fonts: FontDef[]): string {
    if (fonts.length === 0) return "";
    const params = fonts.map(fontFamilyParam).join("&");
    return `https://fonts.googleapis.com/css2?${params}&display=swap`;
}

export const ALL_FONTS_URL = buildGoogleFontsUrl(AVAILABLE_FONTS);

export function buildFontsUrlForNames(names: string[]): string {
    const defs = AVAILABLE_FONTS.filter((f) => names.includes(f.name));
    return buildGoogleFontsUrl(defs);
}

export function parseSegments(raw: string | null | undefined): NameSegment[] | null {
    if (!raw) return null;
    try {
        const parsed = JSON.parse(raw);
        if (Array.isArray(parsed) && parsed.length > 0) return parsed as NameSegment[];
    } catch {
        // ignore malformed JSON
    }
    return null;
}

export function segmentStyle(seg: NameSegment): Record<string, string | number | undefined> {
    return {
        fontFamily: seg.font ? `'${seg.font}', sans-serif` : undefined,
        color: seg.color ?? undefined,
        fontWeight: seg.bold ? 700 : undefined,
        fontStyle: seg.italic ? "italic" : undefined,
    };
}
