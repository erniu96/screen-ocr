#include "CMojoBridge.h"
#include <dlfcn.h>
#include <errno.h>
#include <libproc.h>
#include <signal.h>
#include <stdatomic.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/proc.h>
#include <sys/proc_info.h>
#include <sys/wait.h>
#include <unistd.h>

/* These signatures are the pinned runtime's observed C ABI. In particular,
 * Apple ARM64 passes variadic arguments differently from fixed arguments. */
struct OCRMojo {
    void *library;
    void *environment;
    void *context;
    OCRMojoPayloadCallback payload;
    OCRMojoFailureCallback failure;
    atomic_bool closing;
    void (*configure)(int, ...);
    void (*initialize)(int, const char **);
    void *(*create)(void);
    void (*callback)(void *, int, ...);
    void (*param)(void *, int, ...);
    void (*append)(void *, const char *, const char *);
    void (*start)(void *);
    void (*stop)(void *);
    void (*remove)(void *);
    void *(*read)(void *, uint32_t *);
    void (*remove_read)(void *);
    void *(*new_write)(int, bool, uint32_t);
    void *(*buffer)(void *, size_t);
    bool (*send)(void *, void *);
    void (*remove_write)(void *);
    char *helper_path;
    char *resource_path;
};

static OCRMojo *active_session;
static volatile sig_atomic_t cancellation_signal;
static const char bundle_id[] = "dev.screenocr.native";

static void error_text(char *target, size_t capacity, const char *message) {
    if (capacity) snprintf(target, capacity, "%s", message);
}

static void report_failure(OCRMojo *session, const char *message, size_t count) {
    if (!atomic_load(&session->closing)) session->failure(message, count, session->context);
}

static void push_callback(uint32_t request_id, void *info, void *unused) {
    (void)unused;
    OCRMojo *session = active_session;
    uint32_t count = 0;
    const void *source = session->read(info, &count);
    const char *failure = NULL;
    uint8_t *copy = NULL;
    if (count > 64u * 1024u * 1024u || (count && !source)) {
        failure = "Invalid native response buffer";
    } else if (!atomic_load(&session->closing) && count) {
        copy = malloc(count);
        if (copy) memcpy(copy, source, count);
        else failure = "Cannot allocate native response copy";
    }
    /* The Swift waiter may immediately close the environment after callback
     * delivery, so release native ownership before publishing any event. */
    session->remove_read(info);
    if (failure) report_failure(session, failure, strlen(failure));
    else if (!atomic_load(&session->closing)) session->payload(request_id, copy, count, session->context);
    free(copy);
}

static void connected_callback(bool connected, void *unused) { (void)connected; (void)unused; }
static void launched_callback(void *unused) { (void)unused; }
static void disconnected_callback(void *unused) {
    (void)unused;
    const char message[] = "Native engine disconnected";
    report_failure(active_session, message, sizeof(message) - 1);
}
static void launch_failed_callback(int code, void *unused) {
    (void)unused;
    char message[96];
    snprintf(message, sizeof(message), "Native engine launch failed: %d", code);
    report_failure(active_session, message, strlen(message));
}
static void error_callback(const void *data, int length, void *unused) {
    (void)unused;
    if (length < 0 || (length && !data)) {
        const char message[] = "Invalid native error buffer";
        report_failure(active_session, message, sizeof(message) - 1);
        return;
    }
    report_failure(active_session, data ? data : "", (size_t)(length > 4096 ? 4096 : length));
}

OCRMojo *OCRMojoCreate(const char *library_path, OCRMojoPayloadCallback payload,
                     OCRMojoFailureCallback failure, void *context,
                     char *error, size_t error_capacity) {
    if (active_session) {
        error_text(error, error_capacity, "Only one Mojo session is allowed in a worker process");
        return NULL;
    }
    OCRMojo *session = calloc(1, sizeof(*session));
    if (!session) { error_text(error, error_capacity, "Cannot allocate Mojo session"); return NULL; }
    session->library = dlopen(library_path, RTLD_NOW | RTLD_LOCAL);
    if (!session->library) { error_text(error, error_capacity, dlerror()); free(session); return NULL; }
#define BIND(member, symbol) do { \
    *(void **)(&session->member) = dlsym(session->library, symbol); \
    if (!session->member) { \
        error_text(error, error_capacity, "Missing native symbol: " symbol); \
        dlclose(session->library); free(session); return NULL; \
    } \
} while (0)
    BIND(configure, "SetMMMojoConfiguration");
    BIND(initialize, "InitializeMMMojo");
    BIND(create, "CreateMMMojoEnvironment");
    BIND(callback, "SetMMMojoEnvironmentCallbacks");
    BIND(param, "SetMMMojoEnvironmentInitParams");
    BIND(append, "AppendMMSubProcessSwitchNative");
    BIND(start, "StartMMMojoEnvironment");
    BIND(stop, "StopMMMojoEnvironment");
    BIND(remove, "RemoveMMMojoEnvironment");
    BIND(read, "GetMMMojoReadInfoRequest");
    BIND(remove_read, "RemoveMMMojoReadInfo");
    BIND(new_write, "CreateMMMojoWriteInfo");
    BIND(buffer, "GetMMMojoWriteInfoRequest");
    BIND(send, "SendMMMojoWriteInfo");
    BIND(remove_write, "RemoveMMMojoWriteInfo");
#undef BIND
    session->context = context;
    session->payload = payload;
    session->failure = failure;
    atomic_init(&session->closing, false);
    active_session = session;
    return session;
}

bool OCRMojoOpen(OCRMojo *session, const char *helper_path, const char *engine_path,
                 const char *resource_path, char *error, size_t error_capacity) {
    session->helper_path = strdup(helper_path);
    session->resource_path = strdup(resource_path);
    if (!session->helper_path || !session->resource_path || setenv("SCREEN_OCR_RUNTIME", engine_path, 1)) {
        error_text(error, error_capacity, "Cannot prepare native engine environment"); return false;
    }
    session->configure(1000, bundle_id);
    session->initialize(0, NULL);
    session->environment = session->create();
    if (!session->environment) {
        error_text(error, error_capacity, "CreateMMMojoEnvironment returned null"); return false;
    }
    /* Preserve the verified null user-data slot; callbacks use our one-shot
     * process-global session instead of assuming undocumented context rules. */
    session->callback(session->environment, 0, (void *)NULL);
    session->callback(session->environment, 1, (void *)push_callback);
    session->callback(session->environment, 4, (void *)connected_callback);
    session->callback(session->environment, 5, (void *)disconnected_callback);
    session->callback(session->environment, 6, (void *)launched_callback);
    session->callback(session->environment, 7, (void *)launch_failed_callback);
    session->callback(session->environment, 8, (void *)error_callback);
    session->param(session->environment, 0, 1);
    session->param(session->environment, 2, session->helper_path);
    session->append(session->environment, "no-sandbox", "");
    session->append(session->environment, "base-bundle-id", bundle_id);
    session->append(session->environment, "ocr-resource-root", session->resource_path);
    session->start(session->environment);
    return true;
}

bool OCRMojoSend(OCRMojo *session, const uint8_t *bytes, size_t count,
                 char *error, size_t error_capacity) {
    void *info = session->new_write(1, false, 10010);
    if (!info) { error_text(error, error_capacity, "Cannot allocate Mojo request"); return false; }
    void *target = session->buffer(info, count);
    if (!target) {
        session->remove_write(info);
        error_text(error, error_capacity, "Cannot allocate Mojo payload"); return false;
    }
    memcpy(target, bytes, count);
    /* Send transfers ownership even when it reports failure. */
    if (!session->send(session->environment, info)) {
        error_text(error, error_capacity, "SendMMMojoWriteInfo failed"); return false;
    }
    return true;
}

void OCRMojoClose(OCRMojo *session) {
    atomic_store(&session->closing, true);
    if (session->environment) {
        session->stop(session->environment);
        session->remove(session->environment);
        session->environment = NULL;
    }
    /* No dlclose/free: native callbacks may still be unwinding. */
}

static void cancellation_handler(int signal_number) { cancellation_signal = signal_number; }
bool OCRWorkerInstallSignals(void) {
    struct sigaction action;
    memset(&action, 0, sizeof(action));
    action.sa_handler = cancellation_handler;
    sigemptyset(&action.sa_mask);
    if (sigaction(SIGINT, &action, NULL) || sigaction(SIGTERM, &action, NULL)) return false;
    signal(SIGPIPE, SIG_IGN);
    return true;
}
int OCRWorkerCancellationSignal(void) { return cancellation_signal; }

bool OCRWorkerReapProcessGroup(void) {
    pid_t owner = getpid();
    if (getpgrp() != owner) return false;
    for (unsigned attempt = 0; attempt < 100; attempt++) {
        while (waitpid(-1, NULL, WNOHANG) > 0) {}
        int size = proc_listpids(PROC_PGRP_ONLY, (uint32_t)owner, NULL, 0);
        /* Apple's libproc wrapper returns 0 on __proc_info failure. Our own
         * still-running group must contain at least this worker. */
        if (size <= 0) return false;
        size += 32 * sizeof(pid_t);
        pid_t *pids = calloc(1, (size_t)size);
        if (!pids) return false;
        int bytes = proc_listpids(PROC_PGRP_ONLY, (uint32_t)owner, pids, size);
        if (bytes <= 0 || bytes >= size) { free(pids); return false; }
        bool remaining = false;
        bool found_owner = false;
        for (int index = 0; index < bytes / (int)sizeof(pid_t); index++) {
            pid_t pid = pids[index];
            if (pid == owner) { found_owner = true; continue; }
            if (pid <= 0 || getpgid(pid) != owner) continue;
            struct proc_bsdinfo info;
            int fetched = proc_pidinfo(pid, PROC_PIDTBSDINFO, 0, &info, sizeof(info));
            if (fetched == sizeof(info) && info.pbi_status == SZOMB) continue;
            if (kill(pid, SIGKILL) && errno != ESRCH) { free(pids); return false; }
            remaining = true;
        }
        free(pids);
        if (!found_owner) return false;
        if (!remaining) return true;
        usleep(10000);
    }
    return false;
}
