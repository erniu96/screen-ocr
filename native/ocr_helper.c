#include <dlfcn.h>
#include <stdio.h>
#include <stdlib.h>

/* Runtime 4.2.0 arm64: its entry point forwards argc/argv to InitializeMMMojo.
 * Keep this helper in a separate process: the engine owns its event loop. */
int main(int argc, char **argv) {
    const char *path = getenv("SCREEN_OCR_RUNTIME");
    if (!path || path[0] != '/') {
        fputs("SCREEN_OCR_RUNTIME must name an absolute OCR dylib path\n", stderr);
        return 64;
    }
    void *library = dlopen(path, RTLD_NOW | RTLD_LOCAL);
    if (!library) {
        fprintf(stderr, "Cannot load OCR engine: %s\n", dlerror());
        return 65;
    }
    int (*entry)(int, char **) = (int (*)(int, char **))dlsym(library, "WeChatMain");
    if (!entry) {
        fprintf(stderr, "Missing WeChatMain: %s\n", dlerror());
        return 66;
    }
    /* Do not dlclose: native background threads may still use the library. */
    return entry(argc, argv);
}
