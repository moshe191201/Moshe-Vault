using System.Text.Json;
using OfficeIMO.OneNote;
using OfficeIMO.OneNote.Markdown;

var input = args.Length > 0 ? args[0] : null;
var outDir = args.Length > 1 ? args[1] : null;

if (string.IsNullOrEmpty(input) || string.IsNullOrEmpty(outDir))
{
    Console.Error.WriteLine("Usage: OneNoteOffline <input .one|.onetoc2|.onepkg|dir> <output dir>");
    return 1;
}

if (!File.Exists(input) && !Directory.Exists(input))
{
    Console.Error.WriteLine($"Input not found: {input}");
    return 1;
}

Directory.CreateDirectory(outDir);
var results = new List<object>();

void ProcessSection(OneNoteSection section, string notebookName, string sectionRelPath)
{
    foreach (var page in section.Pages)
    {
        if (page.IsDeleted || page.IsConflictPage || page.IsVersionHistoryPage) continue;

        // Collect binary elements from page graph
        var binaries = new List<OneNoteBinaryElement>();
        CollectBinariesFromPage(page, binaries);

        // Map each binary to stable asset filename
        var assetMap = new Dictionary<OneNoteBinaryElement, string>();
        int idx = 0;
        foreach (var bin in binaries)
        {
            if (bin.Payload == null) continue;
            var ext = Path.GetExtension(bin.FileName ?? "") ?? "";
            if (string.IsNullOrEmpty(ext)) ext = GuessExtension(bin.MediaType);
            if (string.IsNullOrEmpty(ext)) ext = ".bin";
            var safeName = MakeSafeFileName(Path.GetFileNameWithoutExtension(bin.FileName ?? $"asset-{idx}"));
            var fileName = $"{safeName}-{idx}{ext}";
            assetMap[bin] = fileName;
            idx++;
        }

        string? AssetUri(OneNoteBinaryElement el)
        {
            if (assetMap.TryGetValue(el, out var name))
                return $"assets/{name}";
            return null;
        }

        var md = OneNoteMarkdownProjection.ToMarkdown(page, assetUriResolver: AssetUri);

        // Extract payloads
        var assetsDir = Path.Combine(outDir, "assets");
        Directory.CreateDirectory(assetsDir);
        var extracted = new List<object>();
        foreach (var kv in assetMap)
        {
            var bin = kv.Key;
            var fileName = kv.Value;
            var dest = Path.Combine(assetsDir, fileName);
            try
            {
                using var src = bin.Payload!.OpenRead();
                using var dst = File.Create(dest);
                src.CopyTo(dst);
                extracted.Add(new
                {
                    fileName,
                    originalName = bin.FileName,
                    kind = bin is OneNoteMedia ? "media" : bin is OneNoteImage ? "image" : "embedded-file",
                    mediaType = bin.MediaType,
                    extension = Path.GetExtension(fileName),
                    size = new FileInfo(dest).Length,
                    payload = $"assets/{fileName}"
                });
            }
            catch (Exception ex)
            {
                Console.Error.WriteLine($"Warning: failed to extract {fileName}: {ex.Message}");
            }
        }

        var safePageTitle = MakeSafeFileName(string.IsNullOrWhiteSpace(page.Title) ? "untitled" : page.Title);
        var pageFileName = $"{safePageTitle}.md";
        var pageRelPath = Path.Combine(sectionRelPath, pageFileName).Replace('\\', '/');
        var pageOutPath = Path.Combine(outDir, pageRelPath);
        Directory.CreateDirectory(Path.GetDirectoryName(pageOutPath)!);
        File.WriteAllText(pageOutPath, md);

        results.Add(new
        {
            type = "page",
            notebook = notebookName,
            section = section.Name,
            title = page.Title,
            level = page.Level,
            created = page.CreatedUtc,
            lastModified = page.LastModifiedUtc,
            file = pageRelPath,
            markdown = md,
            assets = extracted
        });
    }
}

void ProcessNotebook(OneNoteNotebook notebook)
{
    foreach (var section in notebook.Sections)
        ProcessSection(section, notebook.Name ?? "", MakeSafeFileName(section.Name ?? "section"));
    foreach (var group in notebook.SectionGroups)
        ProcessGroup(group, notebook.Name ?? "", MakeSafeFileName(group.Name ?? "group"));
}

void ProcessGroup(OneNoteSectionGroup group, string notebookName, string groupPath)
{
    foreach (var section in group.Sections)
        ProcessSection(section, notebookName, Path.Combine(groupPath, MakeSafeFileName(section.Name ?? "section")));
    foreach (var sub in group.SectionGroups)
        ProcessGroup(sub, notebookName, Path.Combine(groupPath, MakeSafeFileName(sub.Name ?? "subgroup")));
}

// Dispatch by input type
try
{
    if (Directory.Exists(input))
    {
        var tocs = Directory.GetFiles(input, "*.onetoc2", SearchOption.TopDirectoryOnly);
        if (tocs.Length > 0)
        {
            var notebook = OneNoteNotebookReader.Read(tocs[0]);
            ProcessNotebook(notebook);
        }
        else
        {
            foreach (var one in Directory.GetFiles(input, "*.one", SearchOption.AllDirectories))
            {
                var section = OneNoteSectionReader.Read(one);
                ProcessSection(section, Path.GetFileNameWithoutExtension(input), MakeSafeFileName(section.Name ?? Path.GetFileNameWithoutExtension(one)));
            }
        }
    }
    else
    {
        var ext = Path.GetExtension(input).ToLowerInvariant();
        switch (ext)
        {
            case ".one":
                {
                    var section = OneNoteSectionReader.Read(input);
                    ProcessSection(section, "", "");
                    break;
                }
            case ".onetoc2":
                {
                    var notebook = OneNoteNotebookReader.Read(input);
                    ProcessNotebook(notebook);
                    break;
                }
            case ".onepkg":
                {
                    var notebook = OneNotePackageReader.Read(input);
                    ProcessNotebook(notebook);
                    break;
                }
            default:
                Console.Error.WriteLine($"Unsupported extension: {ext} (expected .one, .onetoc2, .onepkg, or directory)");
                return 1;
        }
    }
}
catch (Exception ex)
{
    Console.Error.WriteLine($"Error processing {input}: {ex}");
    return 1;
}

var manifestPath = Path.Combine(outDir, "manifest.json");
File.WriteAllText(manifestPath, JsonSerializer.Serialize(results, new JsonSerializerOptions { WriteIndented = true }));
Console.WriteLine($"Done: {results.Count} pages -> {outDir} (manifest: manifest.json)");
return 0;

static void CollectBinariesFromPage(OneNotePage page, List<OneNoteBinaryElement> outList)
{
    foreach (var el in page.DirectContent) CollectFromElement(el, outList);
    foreach (var outline in page.Outlines) CollectFromElement(outline, outList);
}

static void CollectFromElement(OneNoteElement el, List<OneNoteBinaryElement> outList)
{
    if (el is OneNoteBinaryElement bin)
    {
        outList.Add(bin);
        return;
    }
    if (el is OneNoteOutline outline)
    {
        foreach (var child in outline.Children) CollectFromElement(child, outList);
        return;
    }
    if (el is OneNoteTable table)
    {
        foreach (var row in table.Rows)
            foreach (var cell in row.Cells)
                foreach (var child in cell.Content)
                    CollectFromElement(child, outList);
        return;
    }
    // Paragraphs and other elements don't directly contain binaries in this model
}

static string GuessExtension(string? mediaType)
{
    if (string.IsNullOrEmpty(mediaType)) return "";
    return mediaType.ToLowerInvariant() switch
    {
        "image/png" => ".png",
        "image/jpeg" => ".jpg",
        "image/gif" => ".gif",
        "image/bmp" => ".bmp",
        "image/tiff" => ".tiff",
        "image/svg+xml" => ".svg",
        "application/pdf" => ".pdf",
        _ => ""
    };
}

static string MakeSafeFileName(string name)
{
    var invalid = Path.GetInvalidFileNameChars();
    var safe = string.Join("_", name.Split(invalid, StringSplitOptions.RemoveEmptyEntries)).Trim();
    if (string.IsNullOrWhiteSpace(safe)) safe = "untitled";
    if (safe.Length > 80) safe = safe[..80];
    return safe;
}
