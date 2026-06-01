package com.vkrauth.app.data.remote

import com.vkrauth.app.data.remote.dto.CourierAvailableResponse
import com.vkrauth.app.data.remote.dto.DownloadRequest
import com.vkrauth.app.data.remote.dto.DownloadResponse
import com.vkrauth.app.data.remote.dto.KeyListResponse
import com.vkrauth.app.data.remote.dto.LoginRequest
import com.vkrauth.app.data.remote.dto.LoginResponse
import com.vkrauth.app.data.remote.dto.MyDataResponse
import com.vkrauth.app.data.remote.dto.OkResponse
import com.vkrauth.app.data.remote.dto.PermitListResponse
import com.vkrauth.app.data.remote.dto.ReaderListResponse
import com.vkrauth.app.data.remote.dto.ReaderResponse
import com.vkrauth.app.data.remote.dto.RefreshRequest
import com.vkrauth.app.data.remote.dto.RegisterDeviceRequest
import com.vkrauth.app.data.remote.dto.RegisterDeviceResponse
import com.vkrauth.app.data.remote.dto.RequestKeyRequest
import com.vkrauth.app.data.remote.dto.RequestKeyResponse
import com.vkrauth.app.data.remote.dto.SubmitReportsRequest
import com.vkrauth.app.data.remote.dto.SubmitReportsResponse
import retrofit2.http.Body
import retrofit2.http.GET
import retrofit2.http.POST
import retrofit2.http.Path
import retrofit2.http.Query

interface ScudApi {

    @POST("api/v1/app/auth/login")
    suspend fun login(@Body req: LoginRequest): LoginResponse

    @POST("api/v1/app/auth/refresh")
    suspend fun refresh(@Body req: RefreshRequest): LoginResponse

    @POST("api/v1/app/auth/register-device")
    suspend fun registerDevice(@Body req: RegisterDeviceRequest): RegisterDeviceResponse

    @POST("api/v1/app/auth/logout")
    suspend fun logout(): OkResponse

    @GET("api/v1/app/my-data")
    suspend fun myData(): MyDataResponse

    @GET("api/v1/app/permits")
    suspend fun permits(): PermitListResponse

    @GET("api/v1/app/permits/{permitId}/keys")
    suspend fun permitKeys(@Path("permitId") permitId: String): KeyListResponse

    @POST("api/v1/app/permits/{permitId}/revoke")
    suspend fun revokePermit(@Path("permitId") permitId: String): OkResponse

    @POST("api/v1/app/keys/request")
    suspend fun requestKey(@Body req: RequestKeyRequest): RequestKeyResponse

    @POST("api/v1/app/keys/{keyId}/revoke-on-server")
    suspend fun revokeKeyOnServer(@Path("keyId") keyIdHex: String): OkResponse

    @GET("api/v1/app/readers/{readerId}")
    suspend fun reader(@Path("readerId") readerIdHex: String): ReaderResponse

    @GET("api/v1/app/readers")
    suspend fun readersByGroup(@Query("group_id") groupId: String): ReaderListResponse

    @GET("api/v1/app/courier/available")
    suspend fun courierAvailable(): CourierAvailableResponse

    @POST("api/v1/app/courier/download")
    suspend fun courierDownload(@Body req: DownloadRequest): DownloadResponse

    @POST("api/v1/app/reports/submit")
    suspend fun submitReports(@Body req: SubmitReportsRequest): SubmitReportsResponse
}
