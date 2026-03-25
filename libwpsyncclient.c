#include "deltasync.h"

#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/stat.h>
#include <time.h>

typedef struct {
    unsigned int count[2];
    unsigned int state[4];
    unsigned char buffer[64];
} MD5_CTX;

static void MD5Init(MD5_CTX *context);
static void MD5Update(MD5_CTX *context, const unsigned char *input, unsigned int inputlen);
static void MD5Final(MD5_CTX *context, unsigned char digest[STRONG_CHECKSUM_SIZE]);
static void MD5Transform(unsigned int state[4], const unsigned char block[64]);
static void MD5Encode(unsigned char *output, const unsigned int *input, unsigned int len);
static void MD5Decode(unsigned int *output, const unsigned char *input, unsigned int len);
static uint32_t adler32(const uint8_t *buf, size_t len);
static bool write_literal(FILE *out, const uint8_t *data, size_t len);
static bool copy_chunk_to_output(FILE *out, const char *temp_path, uint32_t chunk_index, size_t *written);

bool clientCompareFileInfo(const char *file_path, const uint8_t *info, size_t len)
{
    struct stat file_info;
    const char *file_name;
    size_t file_name_len;
    uint32_t remote_size;
    uint32_t remote_mtime;

    if (info == NULL || len < FILE_INFO_TRAILER_SIZE) {
        return false;
    }

    if (stat(file_path, &file_info) != 0) {
        return false;
    }

    file_name = deltasync_basename(file_path);
    file_name_len = len - FILE_INFO_TRAILER_SIZE;
    if (strlen(file_name) != file_name_len || memcmp(info, file_name, file_name_len) != 0) {
        return false;
    }

    remote_size = deltasync_read_u32_le(info + file_name_len);
    remote_mtime = deltasync_read_u32_le(info + file_name_len + WEAK_CHECKSUM_SIZE);

    return file_info.st_size == (off_t)remote_size && file_info.st_mtime == (time_t)remote_mtime;
}

uint8_t *clientTransform(const char *file_path, size_t *len)
{
    struct stat file_info;
    FILE *in;
    uint8_t *message;
    size_t chunk_count;
    size_t chunk_index = 0;
    uint8_t buffer[CHUNK_SIZE];
    size_t bytes_read;

    if (len == NULL) {
        return NULL;
    }

    *len = 0;
    if (stat(file_path, &file_info) != 0) {
        return NULL;
    }

    in = fopen(file_path, "rb");
    if (in == NULL) {
        return NULL;
    }

    if (file_info.st_size <= 0) {
        fclose(in);
        return NULL;
    }

    chunk_count = ((size_t)file_info.st_size + CHUNK_SIZE - 1) / CHUNK_SIZE;
    message = (uint8_t *)malloc(chunk_count * CHUNK_SIGNATURE_SIZE);
    if (message == NULL) {
        fclose(in);
        return NULL;
    }

    while ((bytes_read = fread(buffer, 1, sizeof(buffer), in)) > 0) {
        MD5_CTX md5;
        uint8_t md5_buffer[STRONG_CHECKSUM_SIZE] = {0};
        uint32_t adler32_buffer = adler32(buffer, bytes_read);
        uint8_t *chunk_message = message + (chunk_index * CHUNK_SIGNATURE_SIZE);

        MD5Init(&md5);
        MD5Update(&md5, buffer, (unsigned int)bytes_read);
        MD5Final(&md5, md5_buffer);

        deltasync_write_u32_be(chunk_message, adler32_buffer);
        memcpy(chunk_message + WEAK_CHECKSUM_SIZE, md5_buffer, STRONG_CHECKSUM_SIZE);
        chunk_index += 1;
    }

    if (ferror(in)) {
        free(message);
        fclose(in);
        return NULL;
    }

    fclose(in);
    *len = chunk_index * CHUNK_SIGNATURE_SIZE;
    return message;
}

bool clientPrepareRebuildFile(const char *file_path, const char *temp_path)
{
    FILE *out;
    bool renamed = false;

    if (rename(file_path, temp_path) == 0) {
        renamed = true;
    } else if (errno != ENOENT) {
        return false;
    }

    out = fopen(file_path, "wb");
    if (out == NULL) {
        if (renamed) {
            rename(temp_path, file_path);
        }
        return false;
    }

    return fclose(out) == 0;
}

bool clientRebuildFile(const uint8_t *message, size_t len, size_t *offset, const char *file_path, const char *temp_path)
{
    FILE *out;
    size_t literal_len = 0;
    size_t copied_bytes = 0;
    uint32_t chunk_index;

    if (message == NULL || offset == NULL || len == 0) {
        return false;
    }

    switch ((DeltaSyncMessageType)message[0]) {
        case DELTASYNC_MSG_LITERAL_AND_BLOCK:
            if (len < 1 + WEAK_CHECKSUM_SIZE) {
                return false;
            }
            literal_len = len - 1 - WEAK_CHECKSUM_SIZE;
            chunk_index = deltasync_read_u32_be(message + 1 + literal_len);
            out = fopen(file_path, "ab");
            if (out == NULL) {
                return false;
            }
            if (!write_literal(out, message + 1, literal_len)) {
                fclose(out);
                return false;
            }
            *offset += literal_len;
            if (!copy_chunk_to_output(out, temp_path, chunk_index, &copied_bytes)) {
                fclose(out);
                return false;
            }
            *offset += copied_bytes;
            return fclose(out) == 0;

        case DELTASYNC_MSG_LITERAL_ONLY:
            out = fopen(file_path, "ab");
            if (out == NULL) {
                return false;
            }
            if (!write_literal(out, message + 1, len - 1)) {
                fclose(out);
                return false;
            }
            *offset += len - 1;
            return fclose(out) == 0;

        case DELTASYNC_MSG_BLOCK_ONLY:
            if (len < 1 + WEAK_CHECKSUM_SIZE) {
                return false;
            }
            chunk_index = deltasync_read_u32_be(message + 1);
            out = fopen(file_path, "ab");
            if (out == NULL) {
                return false;
            }
            if (!copy_chunk_to_output(out, temp_path, chunk_index, &copied_bytes)) {
                fclose(out);
                return false;
            }
            *offset += copied_bytes;
            return fclose(out) == 0;

        case DELTASYNC_MSG_END:
            if (len == 1) {
                return false;
            }
            out = fopen(file_path, "ab");
            if (out == NULL) {
                return false;
            }
            if (!write_literal(out, message + 1, len - 1)) {
                fclose(out);
                return false;
            }
            *offset += len - 1;
            if (fclose(out) != 0) {
                return false;
            }
            return false;

        default:
            fprintf(stderr, "Unknown DeltaSync message type: %c\n", message[0]);
            return false;
    }
}

void clientRecover(const char *temp_path)
{
    if (temp_path != NULL) {
        remove(temp_path);
    }
}

static bool write_literal(FILE *out, const uint8_t *data, size_t len)
{
    if (len == 0) {
        return true;
    }

    return fwrite(data, 1, len, out) == len;
}

static bool copy_chunk_to_output(FILE *out, const char *temp_path, uint32_t chunk_index, size_t *written)
{
    FILE *temp_file;
    uint8_t buffer[CHUNK_SIZE];
    size_t bytes_read;

    if (written == NULL) {
        return false;
    }

    *written = 0;
    temp_file = fopen(temp_path, "rb");
    if (temp_file == NULL) {
        return false;
    }

    if (fseek(temp_file, (long)((size_t)chunk_index * CHUNK_SIZE), SEEK_SET) != 0) {
        fclose(temp_file);
        return false;
    }

    bytes_read = fread(buffer, 1, sizeof(buffer), temp_file);
    if (ferror(temp_file)) {
        fclose(temp_file);
        return false;
    }

    if (fclose(temp_file) != 0) {
        return false;
    }

    if (!write_literal(out, buffer, bytes_read)) {
        return false;
    }

    *written = bytes_read;
    return true;
}

static uint32_t adler32(const uint8_t *buf, size_t len)
{
    uint32_t s1 = 0;
    uint32_t s2 = 0;
    size_t i = 0;

    if (len < CHUNK_SIZE) {
        return 0;
    }

    for (; i + 4 <= len; i += 4) {
        s2 += 4 * (s1 + buf[i]) + 3 * buf[i + 1] + 2 * buf[i + 2] + buf[i + 3];
        s1 += buf[i] + buf[i + 1] + buf[i + 2] + buf[i + 3];
    }

    for (; i < len; ++i) {
        s1 += buf[i];
        s2 += s1;
    }

    s1 %= MOD_ADLER;
    s2 %= MOD_ADLER;
    return (s2 << 16) + s1;
}

#define F(x, y, z) ((x & y) | (~x & z))
#define G(x, y, z) ((x & z) | (y & ~z))
#define H(x, y, z) (x ^ y ^ z)
#define I(x, y, z) (y ^ (x | ~z))
#define ROTATE_LEFT(x, n) ((x << n) | (x >> (32 - n)))
#define FF(a, b, c, d, x, s, ac) \
    {                               \
        a += F(b, c, d) + x + ac;   \
        a = ROTATE_LEFT(a, s);      \
        a += b;                     \
    }
#define GG(a, b, c, d, x, s, ac) \
    {                               \
        a += G(b, c, d) + x + ac;   \
        a = ROTATE_LEFT(a, s);      \
        a += b;                     \
    }
#define HH(a, b, c, d, x, s, ac) \
    {                               \
        a += H(b, c, d) + x + ac;   \
        a = ROTATE_LEFT(a, s);      \
        a += b;                     \
    }
#define II(a, b, c, d, x, s, ac) \
    {                               \
        a += I(b, c, d) + x + ac;   \
        a = ROTATE_LEFT(a, s);      \
        a += b;                     \
    }

static const unsigned char PADDING[] = {
    0x80, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
};

static void MD5Init(MD5_CTX *context)
{
    context->count[0] = 0;
    context->count[1] = 0;
    context->state[0] = 0x67452301;
    context->state[1] = 0xEFCDAB89;
    context->state[2] = 0x98BADCFE;
    context->state[3] = 0x10325476;
}

static void MD5Update(MD5_CTX *context, const unsigned char *input, unsigned int inputlen)
{
    unsigned int i = 0;
    unsigned int index = (context->count[0] >> 3) & 0x3F;
    unsigned int partlen = 64 - index;

    context->count[0] += inputlen << 3;
    if (context->count[0] < (inputlen << 3)) {
        context->count[1]++;
    }
    context->count[1] += inputlen >> 29;

    if (inputlen >= partlen) {
        memcpy(&context->buffer[index], input, partlen);
        MD5Transform(context->state, context->buffer);
        for (i = partlen; i + 64 <= inputlen; i += 64) {
            MD5Transform(context->state, &input[i]);
        }
        index = 0;
    }

    memcpy(&context->buffer[index], &input[i], inputlen - i);
}

static void MD5Final(MD5_CTX *context, unsigned char digest[STRONG_CHECKSUM_SIZE])
{
    unsigned int bits[2] = {context->count[0], context->count[1]};
    unsigned int index = (context->count[0] >> 3) & 0x3F;
    unsigned int padlen = (index < 56) ? (56 - index) : (120 - index);
    unsigned char encoded_bits[8];

    MD5Encode(encoded_bits, bits, 8);
    MD5Update(context, PADDING, padlen);
    MD5Update(context, encoded_bits, 8);
    MD5Encode(digest, context->state, STRONG_CHECKSUM_SIZE);
}

static void MD5Encode(unsigned char *output, const unsigned int *input, unsigned int len)
{
    unsigned int i = 0;
    unsigned int j = 0;

    while (j < len) {
        output[j] = input[i] & 0xFF;
        output[j + 1] = (input[i] >> 8) & 0xFF;
        output[j + 2] = (input[i] >> 16) & 0xFF;
        output[j + 3] = (input[i] >> 24) & 0xFF;
        ++i;
        j += 4;
    }
}

static void MD5Decode(unsigned int *output, const unsigned char *input, unsigned int len)
{
    unsigned int i = 0;
    unsigned int j = 0;

    while (j < len) {
        output[i] = (unsigned int)input[j] |
                    ((unsigned int)input[j + 1] << 8) |
                    ((unsigned int)input[j + 2] << 16) |
                    ((unsigned int)input[j + 3] << 24);
        ++i;
        j += 4;
    }
}

static void MD5Transform(unsigned int state[4], const unsigned char block[64])
{
    unsigned int a = state[0];
    unsigned int b = state[1];
    unsigned int c = state[2];
    unsigned int d = state[3];
    unsigned int x[64];

    MD5Decode(x, block, 64);
    FF(a, b, c, d, x[0], 7, 0xd76aa478);
    FF(d, a, b, c, x[1], 12, 0xe8c7b756);
    FF(c, d, a, b, x[2], 17, 0x242070db);
    FF(b, c, d, a, x[3], 22, 0xc1bdceee);
    FF(a, b, c, d, x[4], 7, 0xf57c0faf);
    FF(d, a, b, c, x[5], 12, 0x4787c62a);
    FF(c, d, a, b, x[6], 17, 0xa8304613);
    FF(b, c, d, a, x[7], 22, 0xfd469501);
    FF(a, b, c, d, x[8], 7, 0x698098d8);
    FF(d, a, b, c, x[9], 12, 0x8b44f7af);
    FF(c, d, a, b, x[10], 17, 0xffff5bb1);
    FF(b, c, d, a, x[11], 22, 0x895cd7be);
    FF(a, b, c, d, x[12], 7, 0x6b901122);
    FF(d, a, b, c, x[13], 12, 0xfd987193);
    FF(c, d, a, b, x[14], 17, 0xa679438e);
    FF(b, c, d, a, x[15], 22, 0x49b40821);

    GG(a, b, c, d, x[1], 5, 0xf61e2562);
    GG(d, a, b, c, x[6], 9, 0xc040b340);
    GG(c, d, a, b, x[11], 14, 0x265e5a51);
    GG(b, c, d, a, x[0], 20, 0xe9b6c7aa);
    GG(a, b, c, d, x[5], 5, 0xd62f105d);
    GG(d, a, b, c, x[10], 9, 0x02441453);
    GG(c, d, a, b, x[15], 14, 0xd8a1e681);
    GG(b, c, d, a, x[4], 20, 0xe7d3fbc8);
    GG(a, b, c, d, x[9], 5, 0x21e1cde6);
    GG(d, a, b, c, x[14], 9, 0xc33707d6);
    GG(c, d, a, b, x[3], 14, 0xf4d50d87);
    GG(b, c, d, a, x[8], 20, 0x455a14ed);
    GG(a, b, c, d, x[13], 5, 0xa9e3e905);
    GG(d, a, b, c, x[2], 9, 0xfcefa3f8);
    GG(c, d, a, b, x[7], 14, 0x676f02d9);
    GG(b, c, d, a, x[12], 20, 0x8d2a4c8a);

    HH(a, b, c, d, x[5], 4, 0xfffa3942);
    HH(d, a, b, c, x[8], 11, 0x8771f681);
    HH(c, d, a, b, x[11], 16, 0x6d9d6122);
    HH(b, c, d, a, x[14], 23, 0xfde5380c);
    HH(a, b, c, d, x[1], 4, 0xa4beea44);
    HH(d, a, b, c, x[4], 11, 0x4bdecfa9);
    HH(c, d, a, b, x[7], 16, 0xf6bb4b60);
    HH(b, c, d, a, x[10], 23, 0xbebfbc70);
    HH(a, b, c, d, x[13], 4, 0x289b7ec6);
    HH(d, a, b, c, x[0], 11, 0xeaa127fa);
    HH(c, d, a, b, x[3], 16, 0xd4ef3085);
    HH(b, c, d, a, x[6], 23, 0x04881d05);
    HH(a, b, c, d, x[9], 4, 0xd9d4d039);
    HH(d, a, b, c, x[12], 11, 0xe6db99e5);
    HH(c, d, a, b, x[15], 16, 0x1fa27cf8);
    HH(b, c, d, a, x[2], 23, 0xc4ac5665);

    II(a, b, c, d, x[0], 6, 0xf4292244);
    II(d, a, b, c, x[7], 10, 0x432aff97);
    II(c, d, a, b, x[14], 15, 0xab9423a7);
    II(b, c, d, a, x[5], 21, 0xfc93a039);
    II(a, b, c, d, x[12], 6, 0x655b59c3);
    II(d, a, b, c, x[3], 10, 0x8f0ccc92);
    II(c, d, a, b, x[10], 15, 0xffeff47d);
    II(b, c, d, a, x[1], 21, 0x85845dd1);
    II(a, b, c, d, x[8], 6, 0x6fa87e4f);
    II(d, a, b, c, x[15], 10, 0xfe2ce6e0);
    II(c, d, a, b, x[6], 15, 0xa3014314);
    II(b, c, d, a, x[13], 21, 0x4e0811a1);
    II(a, b, c, d, x[4], 6, 0xf7537e82);
    II(d, a, b, c, x[11], 10, 0xbd3af235);
    II(c, d, a, b, x[2], 15, 0x2ad7d2bb);
    II(b, c, d, a, x[9], 21, 0xeb86d391);

    state[0] += a;
    state[1] += b;
    state[2] += c;
    state[3] += d;
}
