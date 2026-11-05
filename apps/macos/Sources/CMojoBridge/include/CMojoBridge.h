#ifndef SCREEN_OCR_MOJO_BRIDGE_H
#define SCREEN_OCR_MOJO_BRIDGE_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

typedef struct OCRMojo OCRMojo;
typedef void (*OCRMojoPayloadCallback)(uint32_t request_id, const uint8_t *bytes, size_t count, void *context);
typedef void (*OCRMojoFailureCallback)(const char *bytes, size_t count, void *context);

/* One session per worker process. The loaded library and callback context live
 * until process exit, because the native library owns background threads. */
OCRMojo *OCRMojoCreate(const char *library_path, OCRMojoPayloadCallback payload,
                     OCRMojoFailureCallback failure, void *context,
                     char *error, size_t error_capacity);
bool OCRMojoOpen(OCRMojo *session, const char *helper_path, const char *engine_path,
                 const char *resource_path, char *error, size_t error_capacity);
bool OCRMojoSend(OCRMojo *session, const uint8_t *bytes, size_t count,
                 char *error, size_t error_capacity);
void OCRMojoClose(OCRMojo *session);

/* Async-signal-safe signal handlers only publish a cancellation flag. */
bool OCRWorkerInstallSignals(void);
int OCRWorkerCancellationSignal(void);
/* After native shutdown, kill and reap any remaining owned group members.
 * Returns false if cleanup cannot be confirmed within one second. */
bool OCRWorkerReapProcessGroup(void);

#endif
