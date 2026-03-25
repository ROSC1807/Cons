#ifndef DELTASYNC_H
#define DELTASYNC_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <string.h>

enum {
    CHUNK_SIZE = 5120,
    SLIDE_WINDOW_SIZE = 10240,
    WEAK_CHECKSUM_SIZE = 4,
    STRONG_CHECKSUM_SIZE = 16,
    CHUNK_SIGNATURE_SIZE = WEAK_CHECKSUM_SIZE + STRONG_CHECKSUM_SIZE,
    FILE_INFO_TRAILER_SIZE = 8,
    HASH_BUCKET_COUNT = 65536,
    DELTASYNC_MAX_MESSAGE_SIZE = SLIDE_WINDOW_SIZE + 5,
    MOD_ADLER = 65521,
};

typedef enum DeltaSyncMessageType {
    DELTASYNC_MSG_LITERAL_AND_BLOCK = 'a',
    DELTASYNC_MSG_LITERAL_ONLY = 'b',
    DELTASYNC_MSG_BLOCK_ONLY = 'c',
    DELTASYNC_MSG_END = 'e',
} DeltaSyncMessageType;

typedef struct DeltaSyncServerContext DeltaSyncServerContext;

static inline const char *deltasync_basename(const char *path)
{
    const char *name = strrchr(path, '/');
    const char *windows_name = strrchr(path, '\\');

    if (windows_name != NULL && (name == NULL || windows_name > name)) {
        name = windows_name;
    }

    return name == NULL ? path : name + 1;
}

static inline uint32_t deltasync_read_u32_be(const uint8_t *data)
{
    return ((uint32_t)data[0] << 24) |
           ((uint32_t)data[1] << 16) |
           ((uint32_t)data[2] << 8) |
           (uint32_t)data[3];
}

static inline void deltasync_write_u32_be(uint8_t *data, uint32_t value)
{
    data[0] = (uint8_t)((value >> 24) & 0xFF);
    data[1] = (uint8_t)((value >> 16) & 0xFF);
    data[2] = (uint8_t)((value >> 8) & 0xFF);
    data[3] = (uint8_t)(value & 0xFF);
}

static inline uint32_t deltasync_read_u32_le(const uint8_t *data)
{
    return ((uint32_t)data[3] << 24) |
           ((uint32_t)data[2] << 16) |
           ((uint32_t)data[1] << 8) |
           (uint32_t)data[0];
}

static inline void deltasync_write_u32_le(uint8_t *data, uint32_t value)
{
    data[0] = (uint8_t)(value & 0xFF);
    data[1] = (uint8_t)((value >> 8) & 0xFF);
    data[2] = (uint8_t)((value >> 16) & 0xFF);
    data[3] = (uint8_t)((value >> 24) & 0xFF);
}

uint8_t *serverReturnFileInfo(const char *file_path, size_t *len);
DeltaSyncServerContext *serverProcessMessage(const uint8_t *message, size_t len, const char *file_path);
bool serverMainDeltaSync(DeltaSyncServerContext *context, uint8_t *message, const char *file_path, size_t *len, size_t *offset);
void serverRecover(DeltaSyncServerContext *context);

bool clientCompareFileInfo(const char *file_path, const uint8_t *info, size_t len);
uint8_t *clientTransform(const char *file_path, size_t *len);
bool clientPrepareRebuildFile(const char *file_path, const char *temp_path);
bool clientRebuildFile(const uint8_t *message, size_t len, size_t *offset, const char *file_path, const char *temp_path);
void clientRecover(const char *temp_path);

#endif
