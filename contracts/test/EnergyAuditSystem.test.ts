import { expect } from "chai";
import { network } from "hardhat";

const { ethers } = await network.create();

describe("EnergyAuditSystem (TRL 4 Unit Tests)", function () {
  async function deployFixture() {
    const [admin, aggregator, consumer, device, attacker] = await ethers.getSigners();

    const ecoToken = await ethers.deployContract("EcoToken", [admin.address]);
    const ecoCert = await ethers.deployContract("EcoCertificate", [admin.address]);
    const auditSystem = await ethers.deployContract("EnergyAuditSystem", [
      admin.address,
      await ecoToken.getAddress(),
      await ecoCert.getAddress(),
    ]);

    const MINTER_ROLE = await ecoToken.MINTER_ROLE();
    await ecoToken.grantRole(MINTER_ROLE, await auditSystem.getAddress());
    await ecoCert.grantRole(MINTER_ROLE, await auditSystem.getAddress());

    const AGGREGATOR_ROLE = await auditSystem.AGGREGATOR_ROLE();
    await auditSystem.grantRole(AGGREGATOR_ROLE, aggregator.address);

    return { ecoToken, ecoCert, auditSystem, admin, aggregator, consumer, device, attacker };
  }

  async function signReading(
    signer: Awaited<ReturnType<typeof ethers.getSigners>>[number],
    deviceAddr: string,
    energyWh: bigint,
    powerW: bigint,
    timestamp: bigint,
    ipfsCidHash: string
  ) {
    const chainId = (await ethers.provider.getNetwork()).chainId;
    const hash = ethers.solidityPackedKeccak256(
      ["address", "uint64", "uint32", "uint64", "bytes32", "uint256"],
      [deviceAddr, energyWh, powerW, timestamp, ipfsCidHash, chainId]
    );
    return signer.signMessage(ethers.getBytes(hash));
  }

  it("registers device, records signed reading under 150k gas, and provides audit trail", async function () {
    const { auditSystem, aggregator, consumer, device } = await deployFixture();

    await auditSystem.registerDevice(device.address, consumer.address, 0, 10000n);

    const ts = BigInt(Math.floor(Date.now() / 1000));
    const cidHash = ethers.keccak256(ethers.toUtf8Bytes("QmExampleIpfsCid123"));
    const sig = await signReading(device, device.address, 1250n, 2400n, ts, cidHash);

    const tx = await auditSystem
      .connect(aggregator)
      .submitReading(device.address, 1250n, 2400n, ts, cidHash, sig);
    const receipt = await tx.wait();

    expect(receipt).to.not.be.null;
    expect(receipt!.gasUsed).to.be.lessThan(150000n);

    const readings = await auditSystem.getDeviceReadings(device.address);
    expect(readings.length).to.equal(1);
    expect(readings[0].energyWh).to.equal(1250n);
  });

  it("rejects forged signature and unauthorized writer (read-only consumer)", async function () {
    const { auditSystem, aggregator, consumer, device, attacker } = await deployFixture();

    await auditSystem.registerDevice(device.address, consumer.address, 1, 10000n);

    const ts = BigInt(Math.floor(Date.now() / 1000));
    const cidHash = ethers.keccak256(ethers.toUtf8Bytes("QmFakeCid"));
    const fakeSig = await signReading(attacker, device.address, 500n, 1000n, ts, cidHash);

    await expect(
      auditSystem.connect(consumer).submitReading(device.address, 500n, 1000n, ts, cidHash, fakeSig)
    ).to.be.revertedWith("Unauthorized writer");

    await expect(
      auditSystem.connect(aggregator).submitReading(device.address, 500n, 1000n, ts, cidHash, fakeSig)
    ).to.be.revertedWith("Invalid device signature");
  });

  it("awards 100 ECOT and ERC-721 certificate for 10% energy savings", async function () {
    const { ecoToken, ecoCert, auditSystem, aggregator, consumer, device } = await deployFixture();

    await auditSystem.registerDevice(device.address, consumer.address, 0, 10000n);

    await auditSystem
      .connect(aggregator)
      .settlePeriod(device.address, 9000n, "ipfs://QmEcoCertMetadata1");

    const balance = await ecoToken.balanceOf(consumer.address);
    expect(balance).to.equal(ethers.parseUnits("100", 18));
    expect(await ecoCert.balanceOf(consumer.address)).to.equal(1n);
  });

  it("records penalty when consumption exceeds baseline by > 5%", async function () {
    const { auditSystem, aggregator, consumer, device } = await deployFixture();

    await auditSystem.registerDevice(device.address, consumer.address, 2, 10000n);
    await auditSystem.connect(aggregator).settlePeriod(device.address, 11500n, "");

    const penalty = await auditSystem.penaltyScore(consumer.address);
    expect(penalty).to.equal(15n);
  });
});